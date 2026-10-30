# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""NCP entry point: NCP class, lifecycle, and the --all-terminals runner.

Implements the design doc:
  * §3.1 NCP class signature.
  * §4.1 --all-terminals shape, strict value validation, rollup.
  * §4.3 nonzero-exit rule (allowlist empty in v0.1.0).
  * §4.4 subprocess hardening (argv arrays, cross-platform process-
    group / job-tree management, controlled stderr policy,
    concurrent cap-enforcing reader threads with proactive tree
    termination on overflow, is-alive verification, outer process
    cleanup on every exit path including KeyboardInterrupt).
  * §5 ten-step lifecycle.
  * §5.1 exception-safe trace ownership (SDK owns the trace tempfile
    from allocation until a successful `return result`; every
    failure path unlinks it).
  * §10 per-instance binary + version caching.
"""

from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import IO, Any, NamedTuple

from ncp_runtime.discovery import check_runtime_version, resolve_binary
from ncp_runtime.types import (
    NCPSubprocessError,
    NCPTimeoutError,
    Result,
    Terminal,
    TerminalError,
)

# §4.4 rule 8 per-stream output caps for `ncp run`. A legitimate
# --all-terminals output is tens of KB at most; these caps bound
# damage from a misbehaving binary without constraining real use.
_STDOUT_CAP = 16 * 1024 * 1024  # 16 MiB
_STDERR_CAP = 1 * 1024 * 1024  # 1 MiB

_READ_CHUNK = 64 * 1024

# §4.1 closed-enum values for terminal `type`.
_VALID_RESULT_TYPES = frozenset({"Success", "LowConfidence", "Failure"})


class _StreamOutput(NamedTuple):
    """Reader-thread result for one captured subprocess stream."""

    data: bytes
    overflowed: bool
    read_failed: bool


# --- Process-tree termination ------------------------------------------


def _signal_tree_terminate(proc: subprocess.Popen[bytes]) -> None:
    """Signal the whole process tree to die. Does not call proc.wait().

    POSIX: SIGKILL to the process group. `proc.pid` is the PGID
    because `Popen(start_new_session=True)` was used; using the pid
    directly avoids `os.getpgid` lookups that can fail with
    ProcessLookupError once the direct child exits.
    Windows: `taskkill /T /F /PID` to walk and terminate the whole
    tree. NOTE: this uses `subprocess.run(timeout=5)` and can block
    up to 5 seconds; callers from reader threads should treat this
    as a brief (not instantaneous) operation.
    """
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _terminate_tree(proc: subprocess.Popen[bytes]) -> None:
    """Best-effort terminate + reap of the process tree. Main-thread only.

    Each termination step uses bounded waits and suppresses failures,
    so this cannot promise unconditional kill-and-reap in adversarial
    cases (e.g., a hostile binary that re-parents descendants outside
    the group or a system under heavy load). PR 3 tests cover failed
    termination, interruption, direct-child exit with surviving
    descendants, and reader-startup failure paths.

    POSIX: SIGTERM to the group, brief wait, SIGKILL to the group
    (regardless of whether the direct child exited: descendants may
    still be alive in the group), another brief wait, then a direct
    `proc.kill()` + reap fallback if the direct child somehow
    remains alive.
    Windows: `taskkill /T /F` (may block up to 5s), then a direct
    `proc.kill()` + reap fallback.
    Group-pid is `proc.pid` directly (set by `start_new_session=True`
    at spawn); not looked up via `os.getpgid`.

    Does NOT race with any reader-thread `os.killpg`: subprocess
    `proc.wait()` and `proc.kill()` are documented thread-safe.
    """
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        # SIGTERM to group for graceful shutdown of the whole tree.
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        # Escalate: SIGKILL to the group REGARDLESS of whether the
        # direct child exited. If the child reaped but descendants
        # remain, this kills them.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass

    # Last-resort direct-child kill. Runs on both OS branches when
    # group / taskkill termination failed or the direct child is
    # somehow still alive.
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass


# --- Bounded + overflow-signalling pipe reader -------------------------


def _run_reader_body(
    pipe: IO[bytes],
    cap: int,
    proc: subprocess.Popen[bytes],
) -> _StreamOutput:
    """Drain `pipe` until EOF, capping captured bytes at `cap`.

    On FIRST overflow, calls `_signal_tree_terminate(proc)` once.
    Continues draining to a bit-bucket so the pipe cannot block the
    child even if termination is slow. OSError during read is
    tracked separately; the capture is marked untrusted in that case.
    """
    buf = bytearray()
    overflowed = False
    read_failed = False
    signalled = False
    while True:
        try:
            chunk = pipe.read(_READ_CHUNK)
        except OSError:
            read_failed = True
            break
        if not chunk:
            break
        remaining = cap - len(buf)
        if remaining > 0:
            buf.extend(chunk[:remaining])
            if len(chunk) > remaining:
                overflowed = True
        else:
            overflowed = True
        if overflowed and not signalled:
            signalled = True
            _signal_tree_terminate(proc)
    return _StreamOutput(
        data=bytes(buf),
        overflowed=overflowed,
        read_failed=read_failed,
    )


# --- §4.1 strict value validation + rollup -----------------------------


def _validate_terminal(obj: object) -> Terminal:
    """Convert one --all-terminals element to a Terminal. Strict.

    Raises `ValueError` on violation. The caller translates to
    `NCPSubprocessError` with category `strict_validation_failed`.
    Error messages NEVER include raw stdout values beyond bounded
    repr-escaped tokens; §4.4 rule 3 extends to exception-chain
    `__cause__` which is visible in tracebacks.
    """
    if not isinstance(obj, dict):
        raise ValueError(f"terminal element is not a JSON object; got {type(obj).__name__}")

    for key in ("node_id", "brick_id", "step", "type"):
        if key not in obj:
            raise ValueError(f"terminal missing required field {key!r}")

    node_id = obj["node_id"]
    brick_id = obj["brick_id"]
    step = obj["step"]
    type_ = obj["type"]

    if not isinstance(node_id, str):
        raise ValueError(f"terminal field 'node_id' not a string; got {type(node_id).__name__}")
    if not isinstance(brick_id, str):
        raise ValueError(f"terminal field 'brick_id' not a string; got {type(brick_id).__name__}")
    if isinstance(step, bool) or not isinstance(step, int):
        raise ValueError(f"terminal field 'step' not an int; got {type(step).__name__}")
    if not isinstance(type_, str):
        raise ValueError(f"terminal field 'type' not a string; got {type(type_).__name__}")
    if type_ not in _VALID_RESULT_TYPES:
        # Bound and repr-escape the untrusted value: stdout content
        # is untrusted (§4.4 rule 3), and exception chaining would
        # otherwise leak this value into tracebacks even though
        # NCPSubprocessError itself sanitizes its own message.
        safe_type = repr(type_[:64])
        raise ValueError(
            f"terminal field 'type' value {safe_type} is not one of {sorted(_VALID_RESULT_TYPES)}"
        )

    output: Any | None = obj["output"] if "output" in obj else None

    error: TerminalError | None
    if "error" not in obj or obj["error"] is None:
        error = None
    else:
        err_obj = obj["error"]
        if not isinstance(err_obj, dict):
            raise ValueError(
                f"terminal field 'error' is not an object; got {type(err_obj).__name__}"
            )
        err_class = err_obj.get("error_class")
        err_msg = err_obj.get("message")
        if not isinstance(err_class, str):
            raise ValueError(
                f"terminal.error.error_class not a string; got {type(err_class).__name__}"
            )
        if not isinstance(err_msg, str):
            raise ValueError(f"terminal.error.message not a string; got {type(err_msg).__name__}")
        error = TerminalError(error_class=err_class, message=err_msg)

    return Terminal(
        node_id=node_id,
        brick_id=brick_id,
        step=step,
        result_type=type_,
        output=output,
        error=error,
    )


def _rollup(terminals: tuple[Terminal, ...]) -> str:
    """§4.1 rollup rule.

    any Failure            -> "Failure"
    else any LowConfidence -> "LowConfidence"
    else                   -> "Success"
    """
    saw_low = False
    for t in terminals:
        if t.result_type == "Failure":
            return "Failure"
        if t.result_type == "LowConfidence":
            saw_low = True
    return "LowConfidence" if saw_low else "Success"


# --- Public API --------------------------------------------------------


class NCP:
    """SDK entry point. See design doc §3.1.

    One instance per SDK consumer; one subprocess per `.run()` call
    in v0.1.0. Binary and runtime version are resolved on the first
    `.run()` call and cached for the instance's lifetime (§10).
    """

    __slots__ = (
        "_user_binary",
        "_timeout",
        "_resolved_binary",
        "_resolved_version",
    )

    _user_binary: str | Path | None
    _timeout: float
    _resolved_binary: Path | None
    _resolved_version: str | None

    def __init__(
        self,
        *,
        binary: str | Path | None = None,
        timeout: float = 60.0,
    ) -> None:
        # Validate `timeout` eagerly so a bad value fails at
        # construction rather than at first `.run()`.
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError(f"timeout must be a number, got {type(timeout).__name__}")
        if not math.isfinite(float(timeout)) or float(timeout) <= 0:
            raise ValueError(f"timeout must be finite and positive, got {timeout!r}")
        self._user_binary = binary
        self._timeout = float(timeout)
        self._resolved_binary = None
        self._resolved_version = None

    def run(
        self,
        graph: str | Path,
        brick_dir: str | Path,
        inputs: dict[str, Any],
        *,
        brick_map: str | Path | None = None,
        max_steps: int | None = None,
    ) -> Result:
        """Execute an NCP graph. See design doc §5 lifecycle."""
        # Public input validation (lightweight; fail before spawn).
        if not isinstance(inputs, dict):
            raise TypeError(f"inputs must be a dict, got {type(inputs).__name__}")
        if max_steps is not None:
            if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps <= 0:
                raise TypeError(f"max_steps must be a positive int or None, got {max_steps!r}")

        # Steps 1 and 2: resolve binary + check version, cached per instance.
        if self._resolved_binary is None:
            self._resolved_binary = resolve_binary(self._user_binary)
        if self._resolved_version is None:
            self._resolved_version = check_runtime_version(self._resolved_binary)
        binary_path = self._resolved_binary

        # Step 3: serialize inputs. TypeError propagates to the
        # caller BEFORE any tempfile creation or subprocess spawn.
        payload = json.dumps(inputs).encode("utf-8")

        # Step 4 + §5.1 ownership. Both tempfiles are owned by this
        # function until (a) the input is unlinked in the outer
        # `finally`, and (b) the trace is either unlinked on a
        # failure path, or transferred to Result on successful return.
        input_fd, input_name = tempfile.mkstemp(suffix=".json", prefix="ncp-input-")
        input_path = Path(input_name)
        try:
            with os.fdopen(input_fd, "wb") as fh:
                fh.write(payload)

            # Allocate trace tempfile INSIDE the input-cleanup `try`
            # so a trace-allocation failure still unlinks the input.
            trace_fd, trace_name = tempfile.mkstemp(suffix=".jsonl", prefix="ncp-trace-")
            trace_path = Path(trace_name)
            os.close(trace_fd)  # we only need the path

            try:
                return self._run_subprocess(
                    binary_path=binary_path,
                    graph=Path(graph),
                    brick_dir=Path(brick_dir),
                    input_path=input_path,
                    trace_path=trace_path,
                    brick_map=Path(brick_map) if brick_map is not None else None,
                    max_steps=max_steps,
                )
            except BaseException:
                # §5.1: unlink trace on every failure path, including
                # NCPSDKError, unexpected exceptions, and KeyboardInterrupt.
                try:
                    trace_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
        finally:
            # Step 6: delete temp input file on every exit path.
            try:
                input_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _run_subprocess(
        self,
        *,
        binary_path: Path,
        graph: Path,
        brick_dir: Path,
        input_path: Path,
        trace_path: Path,
        brick_map: Path | None,
        max_steps: int | None,
    ) -> Result:
        """Steps 5 through 10. See §4.4 and §5.1."""
        argv: list[str] = [
            str(binary_path),
            "run",
            str(graph),
            "--input",
            str(input_path),
            "--brick-dir",
            str(brick_dir),
            "--trace",
            str(trace_path),
            "--all-terminals",
        ]
        if brick_map is not None:
            argv.extend(["--brick-map", str(brick_map)])
        if max_steps is not None:
            argv.extend(["--max-steps", str(max_steps)])

        # §4.4 rules 1 and 2: argv array; process-group / job tree setup.
        if sys.platform == "win32":
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )

        assert proc.stdout is not None
        assert proc.stderr is not None
        stdout_pipe = proc.stdout
        stderr_pipe = proc.stderr

        stdout_result: list[_StreamOutput] = [
            _StreamOutput(data=b"", overflowed=False, read_failed=False),
        ]
        stderr_result: list[_StreamOutput] = [
            _StreamOutput(data=b"", overflowed=False, read_failed=False),
        ]
        # Reader-thread exception slots. An unexpected exception
        # inside a reader (not just OSError, which the body handles)
        # is captured here so the main thread can reject the capture.
        reader_errors: list[BaseException | None] = [None, None]

        def read_stdout() -> None:
            try:
                stdout_result[0] = _run_reader_body(stdout_pipe, _STDOUT_CAP, proc)
            except BaseException as e:  # noqa: BLE001  (deliberate; see docstring)
                reader_errors[0] = e

        def read_stderr() -> None:
            try:
                stderr_result[0] = _run_reader_body(stderr_pipe, _STDERR_CAP, proc)
            except BaseException as e:  # noqa: BLE001
                reader_errors[1] = e

        t_out: threading.Thread | None = None
        t_err: threading.Thread | None = None
        out_started = False
        err_started = False

        try:
            t_out = threading.Thread(target=read_stdout, name="ncp-run-stdout", daemon=True)
            t_err = threading.Thread(target=read_stderr, name="ncp-run-stderr", daemon=True)
            # Track start() success per thread: Thread.join() on an
            # unstarted thread raises RuntimeError, which would mask
            # the original start failure in the finally block.
            t_out.start()
            out_started = True
            t_err.start()
            err_started = True

            try:
                proc.wait(timeout=self._timeout)
            except subprocess.TimeoutExpired as e:
                _terminate_tree(proc)
                t_out.join(timeout=2)
                t_err.join(timeout=2)
                raise NCPTimeoutError(f"`ncp run` exceeded {self._timeout}s timeout.") from e

            t_out.join(timeout=2)
            t_err.join(timeout=2)

            # Untrusted-capture gate. Any of: reader still alive;
            # reader raised unexpectedly; overflow signalled;
            # OSError during read.
            if (
                t_out.is_alive()
                or t_err.is_alive()
                or reader_errors[0] is not None
                or reader_errors[1] is not None
                or stdout_result[0].overflowed
                or stderr_result[0].overflowed
                or stdout_result[0].read_failed
                or stderr_result[0].read_failed
            ):
                raise NCPSubprocessError(
                    category="output_cap_exceeded",
                    exit_code=proc.returncode,
                    binary_path=binary_path,
                    stderr=stderr_result[0].data,
                )

            stdout_output = stdout_result[0]
            stderr_output = stderr_result[0]

            # §4.3 case 2: any non-zero exit raises. Allowlist empty.
            if proc.returncode != 0:
                raise NCPSubprocessError(
                    category="nonzero_exit",
                    exit_code=proc.returncode,
                    binary_path=binary_path,
                    stderr=stderr_output.data,
                )

            # §4.3 case 3: parse stdout.
            try:
                parsed = json.loads(stdout_output.data)
            except json.JSONDecodeError as e:
                raise NCPSubprocessError(
                    category="malformed_stdout",
                    exit_code=proc.returncode,
                    binary_path=binary_path,
                    stderr=stderr_output.data,
                ) from e

            if not isinstance(parsed, list) or len(parsed) == 0:
                raise NCPSubprocessError(
                    category="malformed_stdout",
                    exit_code=proc.returncode,
                    binary_path=binary_path,
                    stderr=stderr_output.data,
                )

            try:
                terminals = tuple(_validate_terminal(elem) for elem in parsed)
            except ValueError as e:
                raise NCPSubprocessError(
                    category="strict_validation_failed",
                    exit_code=proc.returncode,
                    binary_path=binary_path,
                    stderr=stderr_output.data,
                ) from e

            # Steps 9 and 10: rollup + build Result. Result.__init__
            # attaches weakref.finalize (sole attachment site).
            rollup_type = _rollup(terminals)
            return Result(
                terminals=terminals,
                result_type=rollup_type,
                trace_path=trace_path,
            )
        finally:
            # Best-effort cleanup on every exit path (success, SDK
            # exception, unexpected exception, KeyboardInterrupt,
            # reader-thread start failure). Failed termination is
            # covered by PR 3 tests (per _terminate_tree docstring).
            #
            # Terminate the process tree if:
            #   * proc is still alive (interrupted wait, start-up
            #     failure before wait, etc.), OR
            #   * a started reader is still alive after bounded join
            #     (descendant retaining a pipe past direct-child exit,
            #     so the pipe never EOFed).
            # The second condition is what distinguishes a normal
            # successful run (both readers finished; no termination
            # needed) from the descendant-pipe-retention case
            # (direct child exited with proc.poll() non-None, but
            # readers are stuck).
            out_alive = out_started and t_out is not None and t_out.is_alive()
            err_alive = err_started and t_err is not None and t_err.is_alive()
            if proc.poll() is None or out_alive or err_alive:
                _terminate_tree(proc)

            # Bounded join on readers that successfully started.
            # Joining an unstarted thread raises RuntimeError.
            # Daemon threads will not block process exit if they
            # outlast this join.
            if out_started and t_out is not None:
                t_out.join(timeout=2)
            if err_started and t_err is not None:
                t_err.join(timeout=2)

            # Close pipes.
            try:
                stdout_pipe.close()
            except OSError:
                pass
            try:
                stderr_pipe.close()
            except OSError:
                pass
