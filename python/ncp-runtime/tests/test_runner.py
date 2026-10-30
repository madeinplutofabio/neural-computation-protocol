# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Unit + integration tests for :mod:`ncp_runtime.runner`.

Seven sections:

1. NCP constructor validation (no subprocess).
2. NCP.run input validation (no subprocess; triggers before
   binary resolution).
3. ``_validate_terminal`` strict value checks (direct unit tests).
4. ``_rollup`` rule verification (parametrized over §4.1 table).
5. End-to-end integration against the real ``ncp`` binary via the
   ``ncp_binary`` fixture: locked contract, trace lifecycle,
   input tempfile hygiene, argv passthrough, §4.4 rule 4 path
   hardening.
6. Real-subprocess hardening with ``sys.executable``-based fake
   binaries: process-tree termination of parent AND descendant
   on timeout; pipe-buffer non-deadlock on >16 MiB stdout
   overflow. Only ``check_runtime_version`` is mocked; the SDK's
   actual process-tree termination and reader-thread machinery
   run under real OS semantics on Linux + macOS + Windows.
7. Mocked-subprocess failure branches: timeout, nonzero exit,
   malformed stdout, empty terminal array, strict validation
   failure, output cap exceeded. ``_terminate_tree`` and
   ``_signal_tree_terminate`` are patched to deterministic
   no-ops so Windows ``subprocess.run(["taskkill", ...])``
   cannot re-enter the fake Popen factory.
"""

from __future__ import annotations

import csv
import io
import json
import os
import py_compile
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

from ncp_runtime import (
    NCP,
    NCPSubprocessError,
    NCPTimeoutError,
    Terminal,
    TerminalError,
)
from ncp_runtime.runner import _rollup, _validate_terminal

# ======================================================================
# Section 1: NCP constructor validation
# ======================================================================


def test_ncp_default_construction() -> None:
    """``NCP()`` with no args is valid; binary resolution deferred."""
    ncp = NCP()
    assert ncp is not None


@pytest.mark.parametrize(
    "timeout,exc_type",
    [
        (0.0, ValueError),
        (-1.0, ValueError),
        (float("nan"), ValueError),
        (float("inf"), ValueError),
        (float("-inf"), ValueError),
        (True, TypeError),
        ("60", TypeError),
        (None, TypeError),
    ],
)
def test_ncp_rejects_bad_timeout(timeout: Any, exc_type: type[Exception]) -> None:
    """``timeout`` must be a finite positive non-bool number."""
    with pytest.raises(exc_type):
        NCP(timeout=timeout)


# ======================================================================
# Section 2: NCP.run input validation
# ======================================================================


@pytest.mark.parametrize("inputs", [None, "not a dict", [1, 2, 3], 42])
def test_run_rejects_non_dict_inputs(inputs: Any, tmp_path: Path) -> None:
    """``.run(inputs=...)`` must receive a dict; everything else
    raises TypeError before binary resolution.
    """
    ncp = NCP()
    with pytest.raises(TypeError):
        ncp.run(
            graph=tmp_path / "g.yaml",
            brick_dir=tmp_path,
            inputs=inputs,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("max_steps", [0, -1, True, "100"])
def test_run_rejects_bad_max_steps(max_steps: Any, tmp_path: Path) -> None:
    """``max_steps`` must be None or a positive non-bool int.
    Rejected before binary resolution.
    """
    ncp = NCP()
    with pytest.raises(TypeError):
        ncp.run(
            graph=tmp_path / "g.yaml",
            brick_dir=tmp_path,
            inputs={},
            max_steps=max_steps,
        )


# ======================================================================
# Section 3: _validate_terminal
# ======================================================================


def _valid_terminal_dict() -> dict[str, Any]:
    return {
        "node_id": "n1",
        "brick_id": "b1",
        "step": 0,
        "type": "Success",
        "output": {"message": "hello"},
    }


def test_validate_terminal_happy_path_success() -> None:
    term = _validate_terminal(_valid_terminal_dict())
    assert isinstance(term, Terminal)
    assert term.node_id == "n1"
    assert term.brick_id == "b1"
    assert term.step == 0
    assert term.result_type == "Success"
    assert term.output == {"message": "hello"}
    assert term.error is None


def test_validate_terminal_low_confidence_with_both() -> None:
    """LowConfidence can carry BOTH output AND error per §4.1."""
    d = _valid_terminal_dict()
    d["type"] = "LowConfidence"
    d["error"] = {"error_class": "LOW_CONF", "message": "below threshold"}
    term = _validate_terminal(d)
    assert term.result_type == "LowConfidence"
    assert term.output == {"message": "hello"}
    assert isinstance(term.error, TerminalError)
    assert term.error.error_class == "LOW_CONF"
    assert term.error.message == "below threshold"


def test_validate_terminal_failure_with_error_only() -> None:
    d = {
        "node_id": "n1",
        "brick_id": "b1",
        "step": 0,
        "type": "Failure",
        "error": {"error_class": "INVALID_INPUT", "message": "bad"},
    }
    term = _validate_terminal(d)
    assert term.result_type == "Failure"
    assert term.output is None
    assert isinstance(term.error, TerminalError)


@pytest.mark.parametrize(
    "mutate,msg",
    [
        (lambda d: d.pop("node_id"), "missing required field 'node_id'"),
        (lambda d: d.pop("brick_id"), "missing required field 'brick_id'"),
        (lambda d: d.pop("step"), "missing required field 'step'"),
        (lambda d: d.pop("type"), "missing required field 'type'"),
        (lambda d: d.update(node_id=42), "'node_id' not a string"),
        (lambda d: d.update(brick_id=42), "'brick_id' not a string"),
        (lambda d: d.update(step="0"), "'step' not an int"),
        (lambda d: d.update(step=True), "'step' not an int"),
        (lambda d: d.update(type=42), "'type' not a string"),
    ],
)
def test_validate_terminal_rejects_malformed(mutate: Any, msg: str) -> None:
    d = _valid_terminal_dict()
    mutate(d)
    with pytest.raises(ValueError) as excinfo:
        _validate_terminal(d)
    assert msg in str(excinfo.value)


def test_validate_terminal_rejects_unknown_result_type_bounded() -> None:
    """A ``type`` outside the closed enum raises with the untrusted
    value bounded via repr([:64]).
    """
    d = _valid_terminal_dict()
    d["type"] = "x" * 128
    with pytest.raises(ValueError) as excinfo:
        _validate_terminal(d)
    msg = str(excinfo.value)
    assert "x" * 64 in msg
    assert "x" * 65 not in msg


def test_validate_terminal_non_dict_rejected() -> None:
    with pytest.raises(ValueError):
        _validate_terminal(42)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        _validate_terminal([1, 2])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "error_val,msg_substring",
    [
        ("not a dict", "'error' is not an object"),
        ({"message": "m"}, "error_class not a string"),
        ({"error_class": "C"}, "message not a string"),
        ({"error_class": 1, "message": "m"}, "error_class not a string"),
        ({"error_class": "C", "message": 1}, "message not a string"),
    ],
)
def test_validate_terminal_rejects_malformed_error(error_val: Any, msg_substring: str) -> None:
    d = _valid_terminal_dict()
    d["error"] = error_val
    with pytest.raises(ValueError) as excinfo:
        _validate_terminal(d)
    assert msg_substring in str(excinfo.value)


def test_validate_terminal_output_absent_is_none() -> None:
    d = _valid_terminal_dict()
    d.pop("output")
    term = _validate_terminal(d)
    assert term.output is None


def test_validate_terminal_error_null_is_none() -> None:
    d = _valid_terminal_dict()
    d["error"] = None
    term = _validate_terminal(d)
    assert term.error is None


# ======================================================================
# Section 4: _rollup
# ======================================================================


def _mk(result_type: str, node_id: str = "n") -> Terminal:
    return Terminal(
        node_id=node_id,
        brick_id="b",
        step=0,
        result_type=result_type,
        output=None,
        error=None,
    )


@pytest.mark.parametrize(
    "types,expected",
    [
        ((), "Success"),
        (("Success", "Success"), "Success"),
        (("Success", "LowConfidence"), "LowConfidence"),
        (("LowConfidence", "Failure"), "Failure"),
        (("Failure", "Success"), "Failure"),
    ],
)
def test_rollup(types: tuple[str, ...], expected: str) -> None:
    """§4.1: any Failure -> Failure;
    else any LowConfidence -> LowConfidence; else Success.
    """
    terminals = tuple(_mk(t) for t in types)
    assert _rollup(terminals) == expected


# ======================================================================
# Section 5: End-to-end integration against the real `ncp` binary
# ======================================================================


def test_run_fixture_locked_contract(
    ncp_binary: Path,
    fixture_graph: Path,
    fixture_brick_dir: Path,
) -> None:
    """Locked contract from File 15 approval."""
    ncp = NCP(binary=ncp_binary)
    with ncp.run(
        graph=fixture_graph,
        brick_dir=fixture_brick_dir,
        inputs={"message": "hello"},
    ) as result:
        assert result.result_type == "Success"
        assert len(result.terminals) == 1
        assert result.terminals[0].result_type == "Success"
        assert result.terminals[0].output == {"message": "hello"}


def test_run_trace_file_exists_after_run(
    ncp_binary: Path,
    fixture_graph: Path,
    fixture_brick_dir: Path,
) -> None:
    ncp = NCP(binary=ncp_binary)
    result = ncp.run(
        graph=fixture_graph,
        brick_dir=fixture_brick_dir,
        inputs={"message": "hello"},
    )
    try:
        assert result.trace_path.is_file()
    finally:
        result.cleanup()


def test_run_explicit_cleanup_deletes_trace(
    ncp_binary: Path,
    fixture_graph: Path,
    fixture_brick_dir: Path,
) -> None:
    ncp = NCP(binary=ncp_binary)
    result = ncp.run(
        graph=fixture_graph,
        brick_dir=fixture_brick_dir,
        inputs={"message": "hello"},
    )
    assert result.trace_path.is_file()
    result.cleanup()
    assert not result.trace_path.exists()
    result.cleanup()  # idempotent


def test_run_context_manager_deletes_trace(
    ncp_binary: Path,
    fixture_graph: Path,
    fixture_brick_dir: Path,
) -> None:
    ncp = NCP(binary=ncp_binary)
    with ncp.run(
        graph=fixture_graph,
        brick_dir=fixture_brick_dir,
        inputs={"message": "hello"},
    ) as result:
        trace = result.trace_path
        assert trace.is_file()
    assert not trace.exists()


def test_run_input_tempfile_not_left_behind(
    ncp_binary: Path,
    fixture_graph: Path,
    fixture_brick_dir: Path,
) -> None:
    """§5 step 6: input tempfile unlinked before ``.run()`` returns."""
    tmp = Path(tempfile.gettempdir())
    before = set(tmp.glob("ncp-input-*"))
    ncp = NCP(binary=ncp_binary)
    with ncp.run(
        graph=fixture_graph,
        brick_dir=fixture_brick_dir,
        inputs={"message": "hello"},
    ):
        pass
    after = set(tmp.glob("ncp-input-*"))
    assert after == before, f"input tempfile leaked; new entries: {sorted(after - before)}"


def test_run_argv_passes_max_steps(
    ncp_binary: Path,
    fixture_graph: Path,
    fixture_brick_dir: Path,
) -> None:
    ncp = NCP(binary=ncp_binary)
    with ncp.run(
        graph=fixture_graph,
        brick_dir=fixture_brick_dir,
        inputs={"message": "hello"},
        max_steps=100,
    ) as result:
        assert result.result_type == "Success"


def test_run_handles_path_with_spaces(
    ncp_binary: Path,
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    """§4.4 rule 4: paths with spaces round-trip."""
    dst = tmp_path / "fixtures with spaces"
    shutil.copytree(fixtures_dir, dst)
    ncp = NCP(binary=ncp_binary)
    with ncp.run(
        graph=dst / "graph.yaml",
        brick_dir=dst,
        inputs={"message": "hello"},
    ) as result:
        assert result.result_type == "Success"


def test_run_handles_path_with_non_ascii(
    ncp_binary: Path,
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    """§4.4 rule 4: paths with non-ASCII characters round-trip on
    Windows + Linux + macOS.
    """
    dst = tmp_path / "fixtures-ümläut-日本語"
    shutil.copytree(fixtures_dir, dst)
    ncp = NCP(binary=ncp_binary)
    with ncp.run(
        graph=dst / "graph.yaml",
        brick_dir=dst,
        inputs={"message": "hello"},
    ) as result:
        assert result.result_type == "Success"


# ======================================================================
# Section 6: Real-subprocess hardening
# ======================================================================


def _is_alive(pid: int) -> bool:
    """Cross-platform zombie-aware liveness check.

    Linux: reads ``/proc/<pid>/stat`` and treats state ``Z``
    (zombie, terminated-but-unreaped) and ``X`` (dead) as not
    alive. ``os.kill(pid, 0)`` would return 0 for a zombie, so
    a successful SIGKILL could look like a live process without
    this distinction.

    Windows: parses the PID column from ``tasklist /FO CSV``
    rather than substring-matching, since another PID whose
    digits contain ours would otherwise false-positive.

    macOS and other POSIX without ``/proc``: falls back to
    ``os.kill(pid, 0)``. May briefly return True for an
    unreaped zombie, but launchd reaps orphans quickly.
    """
    if sys.platform == "win32":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        reader = csv.reader(io.StringIO(result.stdout))
        for row in reader:
            if len(row) >= 2 and row[1] == str(pid):
                return True
        return False
    if sys.platform.startswith("linux"):
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        except OSError:
            return False
        rparen = stat.rfind(")")
        # Format: "<pid> (<comm>) <state> ..."; <comm> may contain
        # arbitrary chars including spaces + parens, so anchor on
        # the LAST ')' and read the character two positions later.
        if rparen < 0 or rparen + 2 >= len(stat):
            return False
        state = stat[rparen + 2]
        return state not in ("Z", "X")
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _wait_dead(pid: int, timeout: float = 5.0) -> bool:
    """Poll until the PID is no longer alive, up to ``timeout``
    seconds. Returns True if dead, False on timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_alive(pid):
            return True
        time.sleep(0.1)
    return False


def _best_effort_kill(pid: int) -> None:
    """Fallback-cleanup killer: used ONLY after primary test
    assertions, never as the test's own success criterion.
    """
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True,
                check=False,
            )
        else:
            os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _write_slow_binary_with_descendant(tmp_path: Path, pid_file: Path) -> Path:
    """Create a cross-platform fake ``ncp`` that:

      1. Spawns a sleeping Python grandchild.
      2. Writes BOTH PIDs (own + grandchild) to ``pid_file``.
      3. Sleeps indefinitely until the SDK's timeout kills it.

    The generated Python helper is syntax-checked via
    :mod:`py_compile` immediately after being written, so an
    escaping mistake in ``helper_src`` fails during setup with a
    clear ``PyCompileError`` rather than mid-test with an opaque
    subprocess error.
    """
    # Triple-quoted f-string: {{...}} escapes f-expression braces
    # so they appear literally in the generated source; \\n is
    # the generated file's \n escape (an f-string newline when
    # the helper runs).
    helper_src = f'''\
import os
import subprocess
import sys
import time

pid_file = r"""{pid_file}"""
child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"]
)
with open(pid_file, "w") as f:
    f.write(f"{{os.getpid()}}\\n{{child.pid}}\\n")
    f.flush()
time.sleep(60)
'''
    helper = tmp_path / "fake_ncp_descendant.py"
    helper.write_text(helper_src, encoding="utf-8")
    py_compile.compile(str(helper), doraise=True)

    if sys.platform == "win32":
        shim = tmp_path / "ncp.cmd"
        shim.write_text(
            f'@echo off\r\n"{sys.executable}" "{helper}" %*\r\n',
            encoding="utf-8",
        )
    else:
        shim = tmp_path / "ncp"
        shim.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{helper}" "$@"\n',
            encoding="utf-8",
        )
        shim.chmod(0o755)
    return shim


def _write_overflow_binary(tmp_path: Path, bytes_to_write: int) -> Path:
    """Create a cross-platform fake ``ncp`` that writes
    ``bytes_to_write`` bytes to stdout then exits 0.

    Generated helper is syntax-checked via :mod:`py_compile`.
    """
    helper_src = f"""\
import sys

sys.stdout.buffer.write(b"x" * {bytes_to_write})
sys.stdout.buffer.flush()
sys.exit(0)
"""
    helper = tmp_path / "fake_ncp_overflow.py"
    helper.write_text(helper_src, encoding="utf-8")
    py_compile.compile(str(helper), doraise=True)

    if sys.platform == "win32":
        shim = tmp_path / "ncp.cmd"
        shim.write_text(
            f'@echo off\r\n"{sys.executable}" "{helper}" %*\r\n',
            encoding="utf-8",
        )
    else:
        shim = tmp_path / "ncp"
        shim.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{helper}" "$@"\n',
            encoding="utf-8",
        )
        shim.chmod(0o755)
    return shim


def test_run_real_subprocess_timeout_kills_parent_and_descendant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§4.4 subprocess hardening: on timeout, ``_terminate_tree``
    kills BOTH the direct child AND any descendants it spawned.

    Everything from the SDK invocation onward lives inside one
    outer ``try/finally`` so a wrong-exception or no-exception
    failure of ``pytest.raises`` still runs the fallback cleanup
    and we never leave a sleeping grandchild on the CI runner.
    Primary assertions still run BEFORE the fallback kills;
    otherwise the test could falsely "pass" because its own
    cleanup killed a process the SDK failed to terminate.
    """
    pid_file = tmp_path / "pids.txt"
    binary = _write_slow_binary_with_descendant(tmp_path, pid_file)

    monkeypatch.setattr("ncp_runtime.runner.check_runtime_version", lambda _p: "0.3.6")

    ncp = NCP(binary=binary, timeout=2.0)

    try:
        with pytest.raises(NCPTimeoutError):
            ncp.run(
                graph=tmp_path / "g.yaml",
                brick_dir=tmp_path,
                inputs={"message": "hi"},
            )

        assert pid_file.is_file(), (
            "fake binary did not write pid_file before timeout; "
            "increase timeout or investigate helper spawn delay"
        )
        lines = pid_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2, f"expected 2 PIDs in pid_file, got {len(lines)}: {lines!r}"
        parent_pid, descendant_pid = int(lines[0]), int(lines[1])

        assert _wait_dead(parent_pid), f"parent {parent_pid} still alive 5s after NCPTimeoutError"
        assert _wait_dead(descendant_pid), (
            f"descendant {descendant_pid} still alive 5s after "
            f"NCPTimeoutError (process-tree termination failed to "
            f"walk beyond the direct child)"
        )
    finally:
        # Fallback cleanup: parse pid_file defensively (ignoring
        # malformed lines) and kill any survivors. Runs whether
        # pytest.raises reported mismatch, the SDK didn't raise
        # at all, or an assertion failed.
        if pid_file.is_file():
            for raw in pid_file.read_text(encoding="utf-8").splitlines():
                try:
                    pid = int(raw.strip())
                except ValueError:
                    continue
                if _is_alive(pid):
                    _best_effort_kill(pid)


def test_run_real_subprocess_overflow_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real subprocess writing >16 MiB to stdout triggers the
    overflow-detection path via the concurrent reader thread.

    Strictly stronger evidence than
    ``test_run_mocked_overflow_raises``: a real pipe backs this
    test, so the writer actually blocks on pipe-full and the
    reader thread actually drains concurrently. Without the SDK's
    concurrent reader, the subprocess would deadlock on pipe-full
    and the test would time out instead of raising
    ``output_cap_exceeded``.
    """
    binary = _write_overflow_binary(tmp_path, 20_000_000)

    monkeypatch.setattr("ncp_runtime.runner.check_runtime_version", lambda _p: "0.3.6")

    ncp = NCP(binary=binary, timeout=30.0)
    with pytest.raises(NCPSubprocessError) as excinfo:
        ncp.run(
            graph=tmp_path / "g.yaml",
            brick_dir=tmp_path,
            inputs={"message": "hi"},
        )
    assert excinfo.value.category == "output_cap_exceeded"


# ======================================================================
# Section 7: Mocked-subprocess failure branches
# ======================================================================


class _FakePopen:
    """Mock of :class:`subprocess.Popen` for runner tests.

    Does NOT implement the context-manager protocol. Section 7
    tests patch ``_terminate_tree`` and ``_signal_tree_terminate``
    with deterministic no-ops so Windows ``subprocess.run(
    ["taskkill", ...])`` cannot re-enter this fake factory.
    """

    def __init__(
        self,
        *,
        stdout_bytes: bytes = b"",
        stderr_bytes: bytes = b"",
        exit_code: int = 0,
        wait_times_out: bool = False,
    ) -> None:
        self.stdout: Any = io.BytesIO(stdout_bytes)
        self.stderr: Any = io.BytesIO(stderr_bytes)
        self._exit_code = exit_code
        self._wait_times_out = wait_times_out
        self.returncode: int | None = None
        self.pid = 99999

    def wait(self, timeout: float | None = None) -> int:
        if self._wait_times_out and self.returncode is None:
            raise subprocess.TimeoutExpired(cmd="ncp", timeout=timeout)
        if self.returncode is None:
            self.returncode = self._exit_code
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9

    def poll(self) -> int | None:
        return self.returncode


def _patch_run_mocked(monkeypatch: pytest.MonkeyPatch, **fake_kwargs: Any) -> None:
    """Install the mocked-subprocess scaffolding for Section 7.

    Patches:
      * ``check_runtime_version`` -> no-op returning "0.3.6".
      * ``_terminate_tree`` / ``_signal_tree_terminate`` ->
        deterministic no-ops that mark the fake process as killed.
        REQUIRED on Windows: the real ``_terminate_tree`` calls
        ``subprocess.run(["taskkill", ...])`` which uses Popen as
        a context manager, and ``_FakePopen`` does not implement
        ``__enter__``/``__exit__``.
      * ``subprocess.Popen`` -> factory returning the configured
        ``_FakePopen``.

    Isolation: Section 7 verifies ONLY the exception-
    categorization code paths. Real OS process termination is
    tested in Section 6.
    """
    monkeypatch.setattr("ncp_runtime.runner.check_runtime_version", lambda _p: "0.3.6")

    def fake_terminate(proc: Any) -> None:
        if proc.returncode is None:
            proc.returncode = -9

    monkeypatch.setattr("ncp_runtime.runner._terminate_tree", fake_terminate)
    monkeypatch.setattr("ncp_runtime.runner._signal_tree_terminate", fake_terminate)

    def factory(argv: list[str], **_: Any) -> _FakePopen:
        return _FakePopen(**fake_kwargs)

    monkeypatch.setattr(subprocess, "Popen", factory)


def _mk_ncp(tmp_path: Path) -> NCP:
    binary = tmp_path / "ncp"
    binary.write_bytes(b"")
    return NCP(binary=binary)


def _do_run(ncp: NCP, tmp_path: Path) -> None:
    ncp.run(
        graph=tmp_path / "g.yaml",
        brick_dir=tmp_path,
        inputs={"message": "hi"},
    )


def test_run_mocked_timeout_raises_timeout_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_run_mocked(monkeypatch, wait_times_out=True)
    ncp = _mk_ncp(tmp_path)
    with pytest.raises(NCPTimeoutError):
        _do_run(ncp, tmp_path)


def test_run_mocked_timeout_cleans_trace_tempfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§5.1: trace tempfile unlinked on the timeout path."""
    _patch_run_mocked(monkeypatch, wait_times_out=True)
    ncp = _mk_ncp(tmp_path)
    sys_tmp = Path(tempfile.gettempdir())
    before = set(sys_tmp.glob("ncp-trace-*"))
    with pytest.raises(NCPTimeoutError):
        _do_run(ncp, tmp_path)
    after = set(sys_tmp.glob("ncp-trace-*"))
    assert after == before, f"trace tempfile leaked; new entries: {sorted(after - before)}"


def test_run_mocked_nonzero_exit_raises_subprocess_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_run_mocked(
        monkeypatch,
        stdout_bytes=b"[]",
        stderr_bytes=b"some stderr content",
        exit_code=1,
    )
    ncp = _mk_ncp(tmp_path)
    with pytest.raises(NCPSubprocessError) as excinfo:
        _do_run(ncp, tmp_path)
    assert excinfo.value.category == "nonzero_exit"
    assert excinfo.value.exit_code == 1
    assert excinfo.value.stderr == b"some stderr content"


def test_run_mocked_malformed_stdout_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_run_mocked(monkeypatch, stdout_bytes=b"not json", exit_code=0)
    ncp = _mk_ncp(tmp_path)
    with pytest.raises(NCPSubprocessError) as excinfo:
        _do_run(ncp, tmp_path)
    assert excinfo.value.category == "malformed_stdout"


def test_run_mocked_empty_terminal_array_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§4.3 case 3: empty `[]` is malformed."""
    _patch_run_mocked(monkeypatch, stdout_bytes=b"[]", exit_code=0)
    ncp = _mk_ncp(tmp_path)
    with pytest.raises(NCPSubprocessError) as excinfo:
        _do_run(ncp, tmp_path)
    assert excinfo.value.category == "malformed_stdout"


def test_run_mocked_strict_validation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = json.dumps(
        [
            {
                "node_id": "n",
                "brick_id": "b",
                "step": 0,
                "type": "Weird",
            }
        ]
    ).encode("utf-8")
    _patch_run_mocked(monkeypatch, stdout_bytes=payload, exit_code=0)
    ncp = _mk_ncp(tmp_path)
    with pytest.raises(NCPSubprocessError) as excinfo:
        _do_run(ncp, tmp_path)
    assert excinfo.value.category == "strict_validation_failed"


def test_run_mocked_overflow_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrinks ``_STDOUT_CAP`` to 100 so we don't need a 16 MiB
    payload; the detection code is identical. For stronger
    evidence against pipe-buffer deadlock, see
    :func:`test_run_real_subprocess_overflow_raises` in Section 6.
    """
    monkeypatch.setattr("ncp_runtime.runner._STDOUT_CAP", 100)
    _patch_run_mocked(monkeypatch, stdout_bytes=b"x" * 500, exit_code=0)
    ncp = _mk_ncp(tmp_path)
    with pytest.raises(NCPSubprocessError) as excinfo:
        _do_run(ncp, tmp_path)
    assert excinfo.value.category == "output_cap_exceeded"
