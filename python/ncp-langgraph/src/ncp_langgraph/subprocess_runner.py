# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Subprocess runner: drives `ncp-mcp-server` over stdio JSON-RPC.

This module is the lower-level entry point for invoking an NCP graph
without LangGraph state semantics. :class:`ncp_langgraph.NCPNode` is
the LangGraph-callable wrapper built on top of :func:`call_ncp_graph`.

Per the locked design contract in `docs/LANGGRAPH_ADAPTER.md` (§3.3 +
§5), each :func:`call_ncp_graph` invocation spawns a fresh
`ncp-mcp-server` subprocess, performs the four-frame dialog
(``initialize`` -> ``notifications/initialized`` -> ``tools/list`` ->
``tools/call``), closes stdin gracefully, asserts a clean exit, and
returns a :class:`RunnerResult`.

Lifecycle invariants:

- **One subprocess per call.** No pool, no caching in v0.1.0.
- **Cross-platform timeout** via `threading.Timer` (not `signal.SIGALRM`).
- **stdio encoding is pinned to UTF-8.** MCP JSON is UTF-8 by spec;
  relying on `locale.getencoding()` would silently differ across
  platforms (Windows cp1252, Linux with `LC_ALL=C`, etc.).
- **stderr is captured to a tempfile**, never `subprocess.PIPE`
  (avoids the pipe-buffer deadlock hazard). The parent handle is
  closed immediately after spawn; the child keeps its own dup'd FD.
- **Post-dialog graceful-shutdown gate is mandatory.** Stdin is closed
  and the subprocess is given up to 10s to exit; non-zero returncode
  or graceful stall raises :class:`NCPSubprocessError`. This is the
  regression sentinel for the rmcp tokio time-driver panic discovered
  during Phase 3A.2-E real-host validation; mirrors
  ``examples/mcp/ci_smoke.py``. The gate also runs on clean-protocol
  error paths (ambiguous tool name, zero tools, unknown tool name) so
  the regression sentinel is enforced uniformly.
- **stderr lifecycle:** the tempfile is kept on disk only for
  :class:`NCPSubprocessError`, :class:`NCPTimeoutError`, and unexpected
  `BaseException`. Deleted on success, :class:`NCPInvocationError`
  (graph-level outcome with its own trace file), and
  :class:`NCPAmbiguousToolError` (clean protocol outcome). Empty logs
  are deleted regardless.
- **Strict JSON discipline.** `json.dumps(..., allow_nan=False)` is
  used everywhere to reject NaN / Infinity / -Infinity early instead
  of leaking non-standard values to the Rust side as a parse failure.
- **Library-boundary type checks.** `arguments` must be a dict at
  runtime, not just at type-check time; MCP `tools/call.arguments` is
  specified as an object. `tools/list` entries are validated upfront
  before any name-matching logic.
- **Explicit type-boundary casts.** Values from `json.loads()` and
  `.get()` are `Any` in the type system. After `isinstance(...)`
  narrowing, dict-shaped values are widened to `dict[Any, Any]`, not
  `dict[str, Any]`. The runner uses explicit :func:`typing.cast` at
  every JSON-to-dict boundary to satisfy mypy --strict.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import IO, Any, cast

from ncp_langgraph._version import __version__
from ncp_langgraph.types import (
    NCPAmbiguousToolError,
    NCPInvocationError,
    NCPSubprocessError,
    NCPTimeoutError,
    RunnerResult,
)

# ── constants ─────────────────────────────────────────────────────────

_MCP_PROTOCOL_VERSION = "2025-11-25"
_CLIENT_NAME = "ncp-langgraph"
_POST_DIALOG_WAIT_SECONDS: float = 10.0
_STDERR_TAIL_BYTES = 4096


# ── small helpers ─────────────────────────────────────────────────────


def _resolve_binary(binary: str | Path) -> Path:
    """Resolve the `ncp-mcp-server` binary to a concrete file path.

    For explicit paths (containing a path separator), the file must
    exist AND be a regular file (rejects directories named
    ``ncp-mcp-server``). On Windows, falls back to a ``.exe`` sibling
    when the explicit path has no suffix.

    For bare names, uses :func:`shutil.which` which already honors
    ``PATHEXT`` on Windows (finds ``.exe`` automatically) and returns
    only executable files (not directories).

    Raises :class:`NCPSubprocessError` if no executable file is found.
    """
    p = Path(binary)
    s = str(binary)
    is_explicit_path = os.sep in s or "/" in s
    if is_explicit_path:
        if p.is_file():
            return p.resolve()
        if sys.platform == "win32" and p.suffix == "":
            exe = p.with_suffix(".exe")
            if exe.is_file():
                return exe.resolve()
        raise NCPSubprocessError(
            f"binary not found (or not an executable file) at explicit path: {binary}"
        )
    resolved = shutil.which(s)
    if resolved is None:
        raise NCPSubprocessError(f"binary {binary!r} not found on PATH")
    return Path(resolved)


def _build_argv(
    binary: Path,
    graph: str | Path,
    brick_dir: str | Path,
    trace_dir: str | Path | None,
) -> list[str]:
    """Build the command-line argv for the `ncp-mcp-server` subprocess."""
    argv = [
        str(binary),
        "--graph",
        str(graph),
        "--brick-dir",
        str(brick_dir),
    ]
    if trace_dir is not None:
        argv.extend(["--trace-dir", str(trace_dir)])
    return argv


def _read_stderr_tail(path: Path | None) -> str:
    """Read the last `_STDERR_TAIL_BYTES` of a stderr log file.

    Safe to call on missing/unreadable files: returns empty string.
    Used to enrich exception messages with diagnostic context.
    """
    if path is None or not path.exists():
        return ""
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    tail = data[-_STDERR_TAIL_BYTES:]
    return tail.decode("utf-8", errors="replace")


# ── _DialogDriver ─────────────────────────────────────────────────────


class _DialogDriver:
    """Drives a single MCP stdio dialog against an `ncp-mcp-server` subprocess.

    Owns one Popen handle for the duration of the dialog. Each method
    handles a phase (`initialize`, `notifications/initialized`,
    `tools/list`, `tools/call`) and maps I/O failures to the locked
    typed exceptions:

    - Timer-killed subprocess -> :class:`NCPTimeoutError`
    - JSON-RPC error response or malformed shape -> :class:`NCPSubprocessError`
      (with stderr tail in the message for diagnostics)

    Does NOT own the subprocess lifecycle (spawn / cleanup); that's
    `call_ncp_graph`'s job. The driver only reads/writes through the
    pipes it was handed.
    """

    def __init__(
        self,
        proc: subprocess.Popen[str],
        timeout_state: dict[str, bool],
        stderr_log_path: Path,
    ) -> None:
        # Capture proc.stdin/stdout into locals BEFORE the None-check.
        # Mutable attributes don't narrow safely in mypy because the
        # attribute could be reassigned between the check and the
        # next access; locals do narrow safely.
        stdin = proc.stdin
        stdout = proc.stdout
        if stdin is None or stdout is None:
            raise NCPSubprocessError("subprocess was not spawned with stdin/stdout pipes")
        self._proc: subprocess.Popen[str] = proc
        self._stdin: IO[str] = stdin
        self._stdout: IO[str] = stdout
        self._timeout_state = timeout_state
        self._stderr_log_path = stderr_log_path

    def _stderr_tail(self) -> str:
        return _read_stderr_tail(self._stderr_log_path)

    def _send(self, frame: dict[str, Any]) -> None:
        """Write one JSON-RPC frame to subprocess stdin.

        Strict JSON: NaN / Infinity / -Infinity are rejected here
        (`allow_nan=False`) so non-standard values fail with a clean
        Python-side error rather than leaking to the Rust parser.

        Raises:
            NCPSubprocessError: frame is not JSON-serializable (incl.
                NaN/Infinity), or stdin write/flush fails for a reason
                other than timeout.
            NCPTimeoutError: write failure during a timer-killed state.
        """
        try:
            line = json.dumps(frame, allow_nan=False) + "\n"
        except (TypeError, ValueError) as e:
            raise NCPSubprocessError(f"failed to serialize MCP frame: {e}") from e
        try:
            self._stdin.write(line)
            self._stdin.flush()
        except (BrokenPipeError, OSError) as e:
            if self._timeout_state["fired"]:
                raise NCPTimeoutError(
                    f"timeout exceeded while writing JSON-RPC frame; "
                    f"subprocess was killed by timer.\n"
                    f"stderr tail:\n{self._stderr_tail()}"
                ) from e
            raise NCPSubprocessError(
                f"failed writing JSON-RPC frame to subprocess stdin: {e}\n"
                f"stderr tail:\n{self._stderr_tail()}"
            ) from e

    def _read_response(self, expected_id: int, context: str) -> dict[str, Any]:
        """Read one JSON-RPC response and assert envelope shape.

        Returns the parsed response dict on success. Raises
        :class:`NCPSubprocessError` on any envelope violation
        (jsonrpc!='2.0', `error` field present, id mismatch, non-JSON
        line, non-dict body, non-UTF-8 bytes). Raises
        :class:`NCPTimeoutError` if the timer fired during the read.
        """
        try:
            line = self._stdout.readline()
        except UnicodeDecodeError as e:
            raise NCPSubprocessError(
                f"failed decoding {context} response as UTF-8: {e}\n"
                f"stderr tail:\n{self._stderr_tail()}"
            ) from e
        except OSError as e:
            if self._timeout_state["fired"]:
                raise NCPTimeoutError(
                    f"timeout exceeded while reading {context} response; "
                    f"subprocess was killed by timer.\n"
                    f"stderr tail:\n{self._stderr_tail()}"
                ) from e
            raise NCPSubprocessError(
                f"failed reading {context} response from subprocess stdout: "
                f"{e}\nstderr tail:\n{self._stderr_tail()}"
            ) from e
        if not line:
            if self._timeout_state["fired"]:
                raise NCPTimeoutError(
                    f"timeout exceeded waiting for {context} response; "
                    f"subprocess was killed by timer.\n"
                    f"stderr tail:\n{self._stderr_tail()}"
                )
            raise NCPSubprocessError(
                f"subprocess closed stdout before sending {context} response.\n"
                f"stderr tail:\n{self._stderr_tail()}"
            )
        try:
            resp_raw = json.loads(line)
        except json.JSONDecodeError as e:
            raise NCPSubprocessError(
                f"{context} response is not valid JSON: {e}; line: {line!r}\n"
                f"stderr tail:\n{self._stderr_tail()}"
            ) from e
        if not isinstance(resp_raw, dict):
            raise NCPSubprocessError(
                f"{context} response is not a JSON object: {resp_raw!r}\n"
                f"stderr tail:\n{self._stderr_tail()}"
            )
        # isinstance narrows Any -> dict[Any, Any]; we want dict[str, Any]
        # for the rest of the function. The cast is the standard mypy
        # strict pattern at JSON-to-typed-dict boundaries.
        resp = cast(dict[str, Any], resp_raw)
        if resp.get("jsonrpc") != "2.0":
            raise NCPSubprocessError(
                f"{context} response missing jsonrpc='2.0': {resp}\n"
                f"stderr tail:\n{self._stderr_tail()}"
            )
        if "error" in resp and resp["error"] is not None:
            raise NCPSubprocessError(
                f"{context} returned JSON-RPC error: {resp['error']}\n"
                f"stderr tail:\n{self._stderr_tail()}"
            )
        if resp.get("id") != expected_id:
            raise NCPSubprocessError(
                f"{context} response id mismatch (expected {expected_id}): "
                f"{resp}\nstderr tail:\n{self._stderr_tail()}"
            )
        return resp

    def initialize(self) -> None:
        """Send the `initialize` request and validate the response."""
        self._send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": _MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": _CLIENT_NAME,
                        "version": __version__,
                    },
                },
            }
        )
        resp = self._read_response(expected_id=1, context="initialize")
        if not isinstance(resp.get("result"), dict):
            raise NCPSubprocessError(
                f"initialize response missing result object: {resp}\n"
                f"stderr tail:\n{self._stderr_tail()}"
            )

    def initialized(self) -> None:
        """Send the `notifications/initialized` notification (no response)."""
        self._send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
        )

    def list_tools(self) -> list[Any]:
        """Send `tools/list` and return the tools array from the response.

        Returns `list[Any]` not `list[dict[str, Any]]` because individual
        entries are not validated here. `_resolve_tool_name` performs
        the per-entry validation, which is the correct boundary for
        that work and lets this method stay honest about what came
        off the wire.
        """
        self._send(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            }
        )
        resp = self._read_response(expected_id=2, context="tools/list")
        result = resp.get("result")
        if not isinstance(result, dict):
            raise NCPSubprocessError(
                f"tools/list response missing result object: {resp}\n"
                f"stderr tail:\n{self._stderr_tail()}"
            )
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise NCPSubprocessError(
                f"tools/list result.tools is not a list: {result}\n"
                f"stderr tail:\n{self._stderr_tail()}"
            )
        # isinstance narrows Any -> list[Any] directly; no cast needed.
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Send `tools/call` and return the result object."""
        self._send(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": name,
                    "arguments": arguments,
                },
            }
        )
        resp = self._read_response(expected_id=3, context="tools/call")
        result = resp.get("result")
        if not isinstance(result, dict):
            raise NCPSubprocessError(
                f"tools/call response missing result object: {resp}\n"
                f"stderr tail:\n{self._stderr_tail()}"
            )
        # cast: narrowed dict[Any, Any] -> declared dict[str, Any].
        return cast(dict[str, Any], result)


# ── result validation / extraction helpers ────────────────────────────


def _resolve_tool_name(
    tools: list[Any],
    requested_name: str | None,
) -> str:
    """Pick the MCP tool name to invoke.

    Validates every tool entry from `tools/list` upfront before any
    name-matching logic. `tools/list` is a protocol boundary; silently
    ignoring malformed entries would let a server-side bug slip past
    as if it were clean data.

    Accepts `list[Any]` because :meth:`_DialogDriver.list_tools`
    returns entries without per-entry validation; this function is
    where that validation happens.

    If `requested_name` is provided, verify it appears in the validated
    `tools` array and return it. Otherwise auto-derive: exactly one
    tool -> that tool's name; zero tools or multiple tools -> raise.

    Raises:
        NCPAmbiguousToolError: multiple tools, no `requested_name`.
        NCPSubprocessError: zero tools, requested name not in list,
            or any tool entry is not a `{name: string, ...}` object.
    """
    names: list[str] = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise NCPSubprocessError(f"tools/list returned non-object tool entry: {tool!r}")
        name = tool.get("name")
        if not isinstance(name, str):
            raise NCPSubprocessError(f"tools/list returned a tool with non-string name: {name!r}")
        names.append(name)

    if requested_name is not None:
        if requested_name not in names:
            raise NCPSubprocessError(
                f"requested tool_name={requested_name!r} not in tools/list; "
                f"available tools: {names}"
            )
        return requested_name
    if len(names) == 0:
        raise NCPSubprocessError("tools/list returned zero tools; cannot auto-derive tool_name")
    if len(names) > 1:
        raise NCPAmbiguousToolError(
            f"tools/list returned multiple tools ({names}); "
            f"pass tool_name=... to NCPNode.from_subprocess or call_ncp_graph "
            f"to disambiguate"
        )
    return names[0]


def _validate_call_result_shape(result: dict[str, Any]) -> dict[str, Any]:
    """Validate `tools/call` result + `structuredContent` shape.

    Returns the `structuredContent` dict on success. Raises
    :class:`NCPSubprocessError` on any shape violation. Uses
    key-presence checks (`in`) for required fields so JSON null is
    distinguished from a missing key.
    """
    if "isError" not in result:
        raise NCPSubprocessError(f"tools/call result missing 'isError' field: {result}")
    if not isinstance(result["isError"], bool):
        raise NCPSubprocessError(f"tools/call result.isError is not a bool: {result['isError']!r}")
    if "structuredContent" not in result:
        raise NCPSubprocessError(f"tools/call result missing 'structuredContent' field: {result}")
    sc_raw = result["structuredContent"]
    if not isinstance(sc_raw, dict):
        raise NCPSubprocessError(f"tools/call result.structuredContent is not a dict: {sc_raw!r}")
    # cast: narrowed dict[Any, Any] -> declared dict[str, Any].
    sc = cast(dict[str, Any], sc_raw)
    for required in (
        "result_type",
        "trace_id",
        "trace_path",
        "output_json",
        "terminal_results",
    ):
        if required not in sc:
            raise NCPSubprocessError(f"structuredContent missing required key {required!r}: {sc}")
    if not isinstance(sc["result_type"], str):
        raise NCPSubprocessError(
            f"structuredContent.result_type is not a string: {sc['result_type']!r}"
        )
    allowed_result_types = {"Success", "LowConfidence", "Failure"}
    if sc["result_type"] not in allowed_result_types:
        raise NCPSubprocessError(
            f"structuredContent.result_type is not one of "
            f"{sorted(allowed_result_types)}: {sc['result_type']!r}"
        )
    if result["isError"] is True and sc["result_type"] == "Success":
        raise NCPSubprocessError(
            "tools/call result isError=true but structuredContent.result_type='Success'"
        )
    if result["isError"] is False and sc["result_type"] != "Success":
        raise NCPSubprocessError(
            f"tools/call result isError=false but structuredContent.result_type="
            f"{sc['result_type']!r}"
        )
    if not isinstance(sc["trace_id"], str):
        raise NCPSubprocessError(f"structuredContent.trace_id is not a string: {sc['trace_id']!r}")
    if sc["trace_path"] is not None and not isinstance(sc["trace_path"], str):
        raise NCPSubprocessError(
            f"structuredContent.trace_path is not a string or null: {sc['trace_path']!r}"
        )
    if not isinstance(sc["terminal_results"], list):
        raise NCPSubprocessError(
            f"structuredContent.terminal_results is not a list: {sc['terminal_results']!r}"
        )
    return sc


# ── cleanup helpers ───────────────────────────────────────────────────


def _close_stdin_and_assert_clean_exit(
    proc: subprocess.Popen[str],
    stderr_log_path: Path,
    timeout_state: dict[str, bool],
) -> None:
    """Close stdin and assert the subprocess exits 0 within the post-dialog wait.

    Regression sentinel for the rmcp tokio time-driver panic discovered
    in Phase 3A.2-E real-host validation: rmcp's drain path uses
    `tokio::time::timeout`, which panics if the time driver is not
    enabled. The panic only fires after stdin EOF, AFTER the last
    response has been sent. Dialog-only success assertions would miss
    it. This gate catches it.

    `timeout_state` is consulted on every failure path: if the global
    `threading.Timer` fired during graceful shutdown (the killed
    subprocess could surface as a non-zero returncode or a
    `TimeoutExpired` on `proc.wait`), the failure is mapped to
    :class:`NCPTimeoutError` instead of :class:`NCPSubprocessError`.

    Raises:
        NCPTimeoutError: timer fired during graceful shutdown.
        NCPSubprocessError: graceful shutdown stalled past
            `_POST_DIALOG_WAIT_SECONDS` (timer did not fire), or
            returncode != 0 (timer did not fire).
    """
    if proc.stdin is not None and not proc.stdin.closed:
        try:
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    try:
        returncode = proc.wait(timeout=_POST_DIALOG_WAIT_SECONDS)
    except subprocess.TimeoutExpired as e:
        if timeout_state["fired"]:
            raise NCPTimeoutError(
                f"timeout exceeded during graceful shutdown; "
                f"subprocess was killed by timer.\n"
                f"stderr tail:\n{_read_stderr_tail(stderr_log_path)}"
            ) from e
        raise NCPSubprocessError(
            f"subprocess did not exit within {_POST_DIALOG_WAIT_SECONDS}s "
            f"after stdin close (graceful shutdown stalled).\n"
            f"stderr tail:\n{_read_stderr_tail(stderr_log_path)}"
        ) from e
    if returncode != 0:
        if timeout_state["fired"]:
            raise NCPTimeoutError(
                f"timeout exceeded during graceful shutdown; "
                f"subprocess was killed by timer "
                f"(returncode={returncode}).\n"
                f"stderr tail:\n{_read_stderr_tail(stderr_log_path)}"
            )
        raise NCPSubprocessError(
            f"subprocess exited non-zero after graceful shutdown: "
            f"returncode={returncode}.\n"
            f"stderr tail:\n{_read_stderr_tail(stderr_log_path)}"
        )


def _finally_graceful_cleanup(proc: subprocess.Popen[str]) -> None:
    """Best-effort cleanup for the `finally` block.

    Tries graceful close first (stdin close + wait up to 10s); only
    kills if graceful exit stalls. Safe to call on already-exited
    subprocesses (no-op). **Never raises** -- escaping exceptions here
    would mask the caller's `raised_exception` and skip
    `_cleanup_stderr_log`. Does NOT assert returncode; the regression
    sentinel is enforced only by `_close_stdin_and_assert_clean_exit`.
    """
    if proc.poll() is not None:
        return
    if proc.stdin is not None and not proc.stdin.closed:
        try:
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    # First wait: graceful exit attempt. Catch BOTH OSError and
    # TimeoutExpired so this helper truly never raises -- an OSError
    # leaking here would mask raised_exception and skip the stderr
    # log cleanup in the caller's finally block.
    try:
        proc.wait(timeout=_POST_DIALOG_WAIT_SECONDS)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    # Graceful wait stalled; escalate to kill. `kill()` raises OSError
    # only if the process is already gone or unreachable -- in either
    # case there's nothing left to wait for. `wait()` after kill() can
    # still raise TimeoutExpired (rare: zombie not yet reaped); catch
    # both so this helper truly never raises.
    try:
        proc.kill()
    except OSError:
        return
    try:
        proc.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _cleanup_stderr_log(
    stderr_log_path: Path | None,
    raised_exception: BaseException | None,
) -> None:
    """Delete or keep the stderr tempfile per the locked lifecycle.

    Keep on: :class:`NCPSubprocessError`, :class:`NCPTimeoutError`,
    unexpected `BaseException`.
    Delete on: success (`raised_exception is None`),
    :class:`NCPInvocationError`, :class:`NCPAmbiguousToolError`.

    Override: an empty (0-byte) log is deleted regardless of exception
    type, since an empty log has no diagnostic value.
    """
    if stderr_log_path is None:
        return
    if raised_exception is None:
        keep = False
    elif isinstance(raised_exception, (NCPSubprocessError, NCPTimeoutError)):
        keep = True
    elif isinstance(raised_exception, (NCPInvocationError, NCPAmbiguousToolError)):
        keep = False
    else:
        keep = True
    if keep and stderr_log_path.exists():
        try:
            if stderr_log_path.stat().st_size == 0:
                keep = False
        except OSError:
            pass
    if keep:
        return
    try:
        stderr_log_path.unlink()
    except OSError:
        pass


# ── public call_ncp_graph(...) ────────────────────────────────────────


def call_ncp_graph(
    graph: str | Path,
    brick_dir: str | Path,
    arguments: dict[str, Any],
    *,
    trace_dir: str | Path | None = None,
    binary: str | Path = "ncp-mcp-server",
    tool_name: str | None = None,
    timeout: float = 30.0,
) -> RunnerResult:
    """Invoke an NCP graph by driving `ncp-mcp-server` over stdio.

    Spawns a fresh `ncp-mcp-server` subprocess, performs the locked
    four-frame MCP dialog, returns the result, and shuts the subprocess
    down. Each call is independent; there is no subprocess pool in
    v0.1.0.

    Args:
        graph: Path to the NCP graph YAML manifest. Forwarded to
            `ncp-mcp-server --graph`.
        brick_dir: Path to the directory containing the graph's brick
            WASM artifacts. Forwarded to `ncp-mcp-server --brick-dir`.
        arguments: The arguments object passed to MCP `tools/call`.
            Must be a dict (MCP spec: tools/call.arguments is an
            object) and strict-JSON-serializable (NaN / Infinity
            rejected via `allow_nan=False`).
        trace_dir: If set, forwarded to `ncp-mcp-server --trace-dir`.
            Causes the subprocess to write a JSONL trace file per
            invocation. When `None`, tracing is disabled and
            `RunnerResult.trace["trace_path"]` is `None`.
        binary: Name or absolute path of the `ncp-mcp-server`
            executable. The default ``"ncp-mcp-server"`` resolves
            through `PATH`.
        tool_name: MCP tool name to invoke. If `None`, auto-derived
            from a single-tool ``tools/list`` response; raises
            :class:`ncp_langgraph.NCPAmbiguousToolError` if the server
            exposes multiple tools.
        timeout: Maximum seconds to wait for the subprocess to complete
            the full dialog. Exceeding this raises
            :class:`ncp_langgraph.NCPTimeoutError`.

    Returns:
        A :class:`RunnerResult` with the graph's terminal output,
        the full MCP ``structuredContent``, and the trace metadata
        dict.

    Raises:
        NCPSubprocessError: Spawn failed, non-zero exit, malformed
            JSON-RPC frames, a JSON-RPC error response, `arguments`
            is not a dict, or `arguments` contains non-JSON values.
        NCPInvocationError: The graph ran to a non-success terminal
            (MCP `result.isError == true`).
        NCPTimeoutError: The configured `timeout` was exceeded.
        NCPAmbiguousToolError: `tools/list` returned multiple tools
            and `tool_name` was not provided.
    """
    # ── Pre-flight: arguments must be a dict and strict-JSON-serializable ──
    # MCP `tools/call.arguments` is specified as an object. Catch
    # list/str/None/number at the Python boundary so users get a clean
    # precondition error instead of a confusing JSON-RPC rejection
    # from the Rust side.
    if not isinstance(arguments, dict):
        raise NCPSubprocessError(
            f"MCP tools/call arguments must be a dict/object, got {type(arguments).__name__}"
        )
    try:
        json.dumps(arguments, allow_nan=False)
    except (TypeError, ValueError) as e:
        raise NCPSubprocessError(
            f"failed to serialize MCP tools/call arguments to JSON: {e}"
        ) from e

    # ── Resolve binary + build argv ──
    resolved_binary = _resolve_binary(binary)
    argv = _build_argv(resolved_binary, graph, brick_dir, trace_dir)

    # ── Open stderr tempfile (NOT subprocess.PIPE) ──
    stderr_log = tempfile.NamedTemporaryFile(
        mode="wb",
        prefix="ncp-langgraph-stderr-",
        suffix=".log",
        delete=False,
    )
    stderr_log_path = Path(stderr_log.name)

    proc: subprocess.Popen[str] | None = None
    timer: threading.Timer | None = None
    timeout_state: dict[str, bool] = {"fired": False}
    raised_exception: BaseException | None = None

    try:
        # ── Spawn subprocess ──
        # encoding="utf-8" pins stdio to UTF-8 across platforms (MCP
        # JSON is UTF-8 by spec; locale-default would silently differ
        # on Windows or under `LC_ALL=C`).
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr_log,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except (FileNotFoundError, PermissionError, OSError) as e:
            raise NCPSubprocessError(
                f"failed to spawn ncp-mcp-server subprocess ({resolved_binary}): {e}"
            ) from e
        finally:
            try:
                stderr_log.close()
            except OSError:
                pass

        # ── Post-spawn narrowing guard ──
        # The spawn block declared `proc` as `Popen[str] | None`; from
        # here on the successful path it MUST be non-None. The explicit
        # guard removes mypy's narrowing brittleness across versions
        # and documents the invariant at the language level. `proc`
        # remains the optional outer variable for the finally block;
        # `proc_handle` is the non-None reference used by the dialog
        # and regression-sentinel calls.
        if proc is None:
            raise NCPSubprocessError(
                "failed to spawn ncp-mcp-server subprocess: no process handle returned"
            )
        proc_handle: subprocess.Popen[str] = proc

        # ── Setup timeout ──
        captured_proc: subprocess.Popen[str] = proc_handle

        def on_timeout() -> None:
            timeout_state["fired"] = True
            try:
                captured_proc.kill()
            except OSError:
                pass

        timer = threading.Timer(timeout, on_timeout)
        timer.daemon = True
        timer.start()

        # ── Drive the MCP dialog ──
        driver = _DialogDriver(proc_handle, timeout_state, stderr_log_path)
        driver.initialize()
        driver.initialized()
        tools = driver.list_tools()

        # ── Resolve tool name; run regression sentinel before raising ──
        try:
            resolved_name = _resolve_tool_name(tools, tool_name)
        except (NCPAmbiguousToolError, NCPSubprocessError):
            _close_stdin_and_assert_clean_exit(proc_handle, stderr_log_path, timeout_state)
            raise

        call_result = driver.call_tool(resolved_name, arguments)

        # ── Regression-sentinel: assert clean post-dialog exit ──
        _close_stdin_and_assert_clean_exit(proc_handle, stderr_log_path, timeout_state)

        # ── Extract result OR raise NCPInvocationError ──
        sc = _validate_call_result_shape(call_result)
        if call_result["isError"] is True:
            raise NCPInvocationError(
                f"NCP graph returned non-success terminal: {sc['result_type']}",
                structured_content=sc,
                result_type=sc["result_type"],
                trace_id=sc["trace_id"],
                trace_path=sc["trace_path"],
            )
        return RunnerResult(
            output_json=sc["output_json"],
            structured_content=sc,
            trace={
                "result_type": sc["result_type"],
                "trace_id": sc["trace_id"],
                "trace_path": sc["trace_path"],
            },
        )

    except BaseException as exc:
        raised_exception = exc
        raise
    finally:
        if timer is not None:
            timer.cancel()
        if proc is not None:
            _finally_graceful_cleanup(proc)
        _cleanup_stderr_log(stderr_log_path, raised_exception)
