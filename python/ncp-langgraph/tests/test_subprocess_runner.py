# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Tests for :mod:`ncp_langgraph.subprocess_runner`.

Two layers:

- **Unit tests with mocked Popen.** Patch :func:`subprocess.Popen` and
  :func:`ncp_langgraph.subprocess_runner._resolve_binary` so the dialog
  driver, validation logic, and exception mapping can be tested
  without spawning a real binary. Always run.
- **Integration tests against a real `ncp-mcp-server`.** Gated on the
  ``mcp_server_binary`` fixture (in turn gated on the
  ``NCP_MCP_SERVER`` env var); skipped cleanly when the env var is
  absent. CI exports the env var after building the release binary.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import ncp_langgraph
from ncp_langgraph.subprocess_runner import call_ncp_graph

# ── test helpers ──────────────────────────────────────────────────────


DEFAULT_TOOL_NAME = "test-tool"


def _success_result(
    output_json: Any = None,
    trace_id: str = "trace-uuid",
    trace_path: str | None = None,
) -> dict[str, Any]:
    """Build a `tools/call` result for an isError=false, Success terminal."""
    if output_json is None:
        output_json = {"echoed": "ok"}
    return {
        "isError": False,
        "structuredContent": {
            "result_type": "Success",
            "output_json": output_json,
            "trace_id": trace_id,
            "trace_path": trace_path,
            "terminal_results": [],
        },
        "content": [],
    }


def _failure_result(
    result_type: str = "Failure",
    output_json: Any = None,
    trace_id: str = "trace-uuid",
    trace_path: str | None = None,
) -> dict[str, Any]:
    """Build a `tools/call` result for an isError=true terminal."""
    return {
        "isError": True,
        "structuredContent": {
            "result_type": result_type,
            "output_json": output_json,
            "trace_id": trace_id,
            "trace_path": trace_path,
            "terminal_results": [],
        },
        "content": [],
    }


def _make_init_response() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": "2025-11-25",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "test", "version": "0.0.0"},
        },
    }


def _make_tools_list_response(tools: list[Any] | None = None) -> dict[str, Any]:
    if tools is None:
        tools = [{"name": DEFAULT_TOOL_NAME, "description": "test"}]
    return {"jsonrpc": "2.0", "id": 2, "result": {"tools": tools}}


def _make_call_response(result: dict[str, Any] | None = None) -> dict[str, Any]:
    if result is None:
        result = _success_result()
    return {"jsonrpc": "2.0", "id": 3, "result": result}


def _frames(
    init: dict[str, Any] | None = None,
    tools_list: dict[str, Any] | None = None,
    call: dict[str, Any] | None = None,
) -> list[str]:
    """Build the three response frames the runner reads, in order."""
    frames: list[dict[str, Any]] = [
        init if init is not None else _make_init_response(),
        tools_list if tools_list is not None else _make_tools_list_response(),
        call if call is not None else _make_call_response(),
    ]
    return [json.dumps(f) + "\n" for f in frames]


class _FakeStdin:
    """Test double for the subprocess stdin pipe."""

    def __init__(self) -> None:
        self.writes: list[str] = []
        self.closed = False

    def write(self, data: str) -> int:
        if self.closed:
            raise OSError("write to closed stdin")
        self.writes.append(data)
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class _FakeStdout:
    """Test double for the subprocess stdout pipe."""

    def __init__(self, lines: list[str]) -> None:
        self._iter = iter(lines)

    def readline(self) -> str:
        try:
            return next(self._iter)
        except StopIteration:
            return ""


class _FakePopen:
    """Test double for subprocess.Popen[str].

    Configured per-test with stdout response frames, returncode, and
    optional wait/kill side effects. ``wait_side_effect`` runs at the
    start of every ``wait()`` call -- used for timer-triggering tests
    where the timer must fire mid-wait.
    """

    def __init__(
        self,
        stdout_lines: list[str],
        returncode: int = 0,
        wait_timeout_first: bool = False,
        wait_side_effect: Callable[[], None] | None = None,
    ) -> None:
        self._returncode = returncode
        self._wait_timeout_first = wait_timeout_first
        self._wait_side_effect = wait_side_effect
        self._exited = False
        self.stdin: _FakeStdin | None = _FakeStdin()
        self.stdout: _FakeStdout = _FakeStdout(stdout_lines)
        self.kill_called = False
        self.argv: list[str] | None = None
        self.popen_kwargs: dict[str, Any] = {}

    def wait(self, timeout: float | None = None) -> int:
        if self._wait_side_effect is not None:
            self._wait_side_effect()
        if self._wait_timeout_first:
            self._wait_timeout_first = False
            raise subprocess.TimeoutExpired(cmd=[], timeout=timeout or 0.0)
        self._exited = True
        return self._returncode

    def poll(self) -> int | None:
        return self._returncode if self._exited else None

    def kill(self) -> None:
        self.kill_called = True
        self._exited = True


class _ImmediateTimer:
    """Test double for threading.Timer that fires the callback on start()."""

    daemon = True

    def __init__(self, _interval: float, function: Callable[[], None]) -> None:
        self._function = function

    def start(self) -> None:
        self._function()

    def cancel(self) -> None:
        pass


class _CapturingTimer:
    """Test double for threading.Timer that captures the callback without
    firing it. Tests trigger the callback manually via the class-level
    ``last_callback`` slot.
    """

    daemon = True
    last_callback: Callable[[], None] | None = None

    def __init__(self, _interval: float, function: Callable[[], None]) -> None:
        _CapturingTimer.last_callback = function

    def start(self) -> None:
        pass

    def cancel(self) -> None:
        pass


def _patch_popen_and_binary(
    monkeypatch: pytest.MonkeyPatch,
    fake_popen: _FakePopen,
    binary_path: Path | None = None,
) -> _FakePopen:
    """Patch subprocess.Popen + _resolve_binary so the test never
    touches the filesystem or spawns a real subprocess.
    """
    if binary_path is None:
        binary_path = Path("/fake/ncp-mcp-server")

    def fake_resolve_binary(_binary: Any) -> Path:
        return binary_path

    def fake_factory(*args: Any, **kwargs: Any) -> _FakePopen:
        if args:
            fake_popen.argv = list(args[0])
        elif "args" in kwargs:
            fake_popen.argv = list(kwargs["args"])
        fake_popen.popen_kwargs = kwargs
        return fake_popen

    monkeypatch.setattr("ncp_langgraph.subprocess_runner._resolve_binary", fake_resolve_binary)
    monkeypatch.setattr("subprocess.Popen", fake_factory)
    return fake_popen


# ── happy path ────────────────────────────────────────────────────────


def test_returns_runner_result_with_success_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakePopen(_frames())
    _patch_popen_and_binary(monkeypatch, fake)

    result = call_ncp_graph(
        graph="graph.yaml",
        brick_dir="bricks/",
        arguments={"input": "hi"},
    )

    assert isinstance(result, ncp_langgraph.RunnerResult)
    assert result.output_json == {"echoed": "ok"}
    assert result.trace["result_type"] == "Success"
    assert result.trace["trace_id"] == "trace-uuid"
    assert result.trace["trace_path"] is None
    assert result.structured_content["result_type"] == "Success"
    # Success-path shutdown sentinel: the runner closed stdin and the
    # subprocess exited cleanly. The error paths assert this; the
    # happy path should too, so the regression-sentinel contract is
    # locked end-to-end.
    assert fake._exited
    assert fake.stdin is not None
    assert fake.stdin.closed


def test_explicit_tool_name_overrides_auto_derive(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakePopen(_frames())
    _patch_popen_and_binary(monkeypatch, fake)

    call_ncp_graph(
        graph="g.yaml",
        brick_dir="b/",
        arguments={},
        tool_name=DEFAULT_TOOL_NAME,
    )

    assert fake.stdin is not None
    tools_call_frame = json.loads(fake.stdin.writes[-1])
    assert tools_call_frame["params"]["name"] == DEFAULT_TOOL_NAME


def test_explicit_tool_name_disambiguates_multiple_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reason `tool_name` exists: disambiguate when tools/list
    returns multiple tools. A single-tool fake list with the same name
    passed in does NOT prove this contract; this test does.
    """
    fake = _FakePopen(
        _frames(
            tools_list=_make_tools_list_response(
                tools=[{"name": "a"}, {"name": "b"}],
            ),
        ),
    )
    _patch_popen_and_binary(monkeypatch, fake)

    call_ncp_graph(
        graph="g.yaml",
        brick_dir="b/",
        arguments={},
        tool_name="b",
    )

    assert fake.stdin is not None
    tools_call_frame = json.loads(fake.stdin.writes[-1])
    assert tools_call_frame["params"]["name"] == "b"


def test_trace_path_string_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    call = _make_call_response(_success_result(trace_path="/tmp/abc.jsonl"))
    fake = _FakePopen(_frames(call=call))
    _patch_popen_and_binary(monkeypatch, fake)

    result = call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})

    assert result.trace["trace_path"] == "/tmp/abc.jsonl"


# ── MCP frame sequence + Popen invocation ────────────────────────────


def test_dialog_writes_four_frames_in_correct_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Locked 4-frame sequence: initialize, notifications/initialized,
    tools/list, tools/call. Without this test the implementation could
    silently skip ``notifications/initialized`` and most other unit
    tests would still pass.
    """
    fake = _FakePopen(_frames())
    _patch_popen_and_binary(monkeypatch, fake)

    call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={"x": 1})

    assert fake.stdin is not None
    writes = fake.stdin.writes
    assert len(writes) == 4

    frames = [json.loads(w) for w in writes]

    # Frame 1: initialize
    assert frames[0]["method"] == "initialize"
    assert frames[0]["id"] == 1
    assert frames[0]["params"]["protocolVersion"] == "2025-11-25"
    assert frames[0]["params"]["clientInfo"]["name"] == "ncp-langgraph"
    # Lock-in: clientInfo.version must be sourced from _version.__version__
    # (the private module that exists to break the circular import risk).
    # Without this assertion, refactoring could silently break the MCP
    # protocol version field.
    assert frames[0]["params"]["clientInfo"]["version"] == ncp_langgraph.__version__

    # Frame 2: notifications/initialized (no id)
    assert frames[1]["method"] == "notifications/initialized"
    assert "id" not in frames[1]

    # Frame 3: tools/list
    assert frames[2]["method"] == "tools/list"
    assert frames[2]["id"] == 2

    # Frame 4: tools/call
    assert frames[3]["method"] == "tools/call"
    assert frames[3]["id"] == 3
    assert frames[3]["params"]["name"] == DEFAULT_TOOL_NAME
    assert frames[3]["params"]["arguments"] == {"x": 1}


def test_subprocess_spawned_with_utf8_and_pipe_discipline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UTF-8 encoding is pinned; stdin/stdout are subprocess.PIPE;
    stderr is NOT subprocess.PIPE (the file-backed-stderr deadlock
    prevention is a locked design invariant).
    """
    fake = _FakePopen(_frames())
    _patch_popen_and_binary(monkeypatch, fake)

    call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})

    assert fake.popen_kwargs.get("encoding") == "utf-8"
    assert fake.popen_kwargs.get("text") is True
    assert fake.popen_kwargs.get("stdin") is subprocess.PIPE
    assert fake.popen_kwargs.get("stdout") is subprocess.PIPE
    assert fake.popen_kwargs.get("stderr") is not subprocess.PIPE


# ── argv / arguments forwarding ──────────────────────────────────────


def test_trace_dir_set_forwards_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakePopen(_frames())
    _patch_popen_and_binary(monkeypatch, fake)

    call_ncp_graph(
        graph="g.yaml",
        brick_dir="b/",
        arguments={},
        trace_dir="/tmp/traces",
    )

    assert fake.argv is not None
    assert "--trace-dir" in fake.argv
    idx = fake.argv.index("--trace-dir")
    assert fake.argv[idx + 1] == "/tmp/traces"


def test_trace_dir_none_omits_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakePopen(_frames())
    _patch_popen_and_binary(monkeypatch, fake)

    call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={}, trace_dir=None)

    assert fake.argv is not None
    assert "--trace-dir" not in fake.argv


def test_graph_and_brick_dir_passed_to_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakePopen(_frames())
    _patch_popen_and_binary(monkeypatch, fake)

    call_ncp_graph(graph="my-graph.yaml", brick_dir="my-bricks/", arguments={})

    assert fake.argv is not None
    assert "--graph" in fake.argv
    g_idx = fake.argv.index("--graph")
    assert fake.argv[g_idx + 1] == "my-graph.yaml"
    assert "--brick-dir" in fake.argv
    b_idx = fake.argv.index("--brick-dir")
    assert fake.argv[b_idx + 1] == "my-bricks/"


def test_arguments_passed_to_tools_call_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakePopen(_frames())
    _patch_popen_and_binary(monkeypatch, fake)

    call_ncp_graph(
        graph="g.yaml",
        brick_dir="b/",
        arguments={"x": 1, "nested": {"y": "z"}},
    )

    assert fake.stdin is not None
    tools_call_frame = json.loads(fake.stdin.writes[-1])
    assert tools_call_frame["params"]["arguments"] == {"x": 1, "nested": {"y": "z"}}


# ── input precondition errors ────────────────────────────────────────


def test_non_dict_arguments_raise_subprocess_error_before_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen_called = False

    def fail_popen(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal popen_called
        popen_called = True
        raise AssertionError("subprocess.Popen must not be called")

    monkeypatch.setattr("subprocess.Popen", fail_popen)

    for bad in ([1, 2], "string", None, 42):
        with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
            call_ncp_graph(
                graph="g.yaml",
                brick_dir="b/",
                arguments=bad,  # type: ignore[arg-type]
            )
        assert "must be a dict/object" in str(exc_info.value)
    assert popen_called is False


def test_nan_in_arguments_raises_subprocess_error_with_clean_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_popen(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("subprocess.Popen must not be called")

    monkeypatch.setattr("subprocess.Popen", fail_popen)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(
            graph="g.yaml",
            brick_dir="b/",
            arguments={"x": float("nan")},
        )
    assert "failed to serialize" in str(exc_info.value).lower()


# ── tools/list errors (run regression sentinel before raising) ───────


def test_multi_tool_no_name_raises_ambiguous_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakePopen(
        _frames(
            tools_list=_make_tools_list_response(tools=[{"name": "a"}, {"name": "b"}]),
        ),
    )
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPAmbiguousToolError):
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})

    assert fake._exited
    assert fake.stdin is not None
    assert fake.stdin.closed


def test_zero_tools_raises_subprocess_error(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakePopen(_frames(tools_list=_make_tools_list_response(tools=[])))
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert "zero tools" in str(exc_info.value)
    assert fake._exited
    assert fake.stdin is not None
    assert fake.stdin.closed


def test_tool_name_not_in_list_raises_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePopen(_frames())
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(
            graph="g.yaml",
            brick_dir="b/",
            arguments={},
            tool_name="not-in-list",
        )
    assert "not in tools/list" in str(exc_info.value)
    assert fake._exited
    assert fake.stdin is not None
    assert fake.stdin.closed


def test_non_object_tool_entry_raises_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePopen(_frames(tools_list=_make_tools_list_response(tools=["not-a-dict"])))
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert "non-object tool entry" in str(exc_info.value)
    assert fake._exited
    assert fake.stdin is not None
    assert fake.stdin.closed


def test_non_string_tool_name_raises_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePopen(_frames(tools_list=_make_tools_list_response(tools=[{"name": 42}])))
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert "non-string name" in str(exc_info.value)
    assert fake._exited
    assert fake.stdin is not None
    assert fake.stdin.closed


# ── tools/call result shape validation ───────────────────────────────


def test_missing_is_error_raises_subprocess_error(monkeypatch: pytest.MonkeyPatch) -> None:
    bad_result = {
        "structuredContent": _success_result()["structuredContent"],
        "content": [],
    }
    fake = _FakePopen(_frames(call=_make_call_response(bad_result)))
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert "isError" in str(exc_info.value)


def test_missing_structured_content_raises_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad_result = {"isError": False, "content": []}
    fake = _FakePopen(_frames(call=_make_call_response(bad_result)))
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert "structuredContent" in str(exc_info.value)


def test_non_dict_structured_content_raises_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad_result = {"isError": False, "structuredContent": "not a dict", "content": []}
    fake = _FakePopen(_frames(call=_make_call_response(bad_result)))
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert "structuredContent" in str(exc_info.value)


def test_missing_output_json_raises_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sc = {
        "result_type": "Success",
        "trace_id": "id",
        "trace_path": None,
        "terminal_results": [],
    }
    bad_result = {"isError": False, "structuredContent": sc, "content": []}
    fake = _FakePopen(_frames(call=_make_call_response(bad_result)))
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert "output_json" in str(exc_info.value)


def test_output_json_none_accepted_when_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure-only case carries output_json: null (valid per §5)."""
    fake = _FakePopen(
        _frames(call=_make_call_response(_failure_result(output_json=None))),
    )
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPInvocationError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert exc_info.value.result_type == "Failure"
    assert exc_info.value.structured_content["output_json"] is None


def test_missing_trace_id_raises_subprocess_error(monkeypatch: pytest.MonkeyPatch) -> None:
    sc = {
        "result_type": "Success",
        "output_json": {},
        "trace_path": None,
        "terminal_results": [],
    }
    bad_result = {"isError": False, "structuredContent": sc, "content": []}
    fake = _FakePopen(_frames(call=_make_call_response(bad_result)))
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert "trace_id" in str(exc_info.value)


def test_invalid_trace_id_type_raises_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = _success_result()
    bad["structuredContent"]["trace_id"] = 123  # not str
    fake = _FakePopen(_frames(call=_make_call_response(bad)))
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert "trace_id" in str(exc_info.value)


def test_invalid_trace_path_type_raises_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """trace_path must be str or None; an int is rejected."""
    bad = _success_result()
    bad["structuredContent"]["trace_path"] = 123
    fake = _FakePopen(_frames(call=_make_call_response(bad)))
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert "trace_path" in str(exc_info.value)


def test_missing_terminal_results_raises_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sc = {
        "result_type": "Success",
        "output_json": {},
        "trace_id": "id",
        "trace_path": None,
        # No terminal_results
    }
    bad_result = {"isError": False, "structuredContent": sc, "content": []}
    fake = _FakePopen(_frames(call=_make_call_response(bad_result)))
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert "terminal_results" in str(exc_info.value)


def test_terminal_results_must_be_list(monkeypatch: pytest.MonkeyPatch) -> None:
    sc = {
        "result_type": "Success",
        "output_json": {},
        "trace_id": "id",
        "trace_path": None,
        "terminal_results": {"not": "a list"},
    }
    bad_result = {"isError": False, "structuredContent": sc, "content": []}
    fake = _FakePopen(_frames(call=_make_call_response(bad_result)))
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert "terminal_results" in str(exc_info.value)


def test_unknown_result_type_raises_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = _success_result()
    bad["structuredContent"]["result_type"] = "Bogus"
    fake = _FakePopen(_frames(call=_make_call_response(bad)))
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert "not one of" in str(exc_info.value)


def test_is_error_true_with_success_result_type_raises_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Server bug: isError=true should never be paired with Success."""
    sc = _success_result()["structuredContent"]
    bad_result = {"isError": True, "structuredContent": sc, "content": []}
    fake = _FakePopen(_frames(call=_make_call_response(bad_result)))
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert "isError=true" in str(exc_info.value)
    assert "Success" in str(exc_info.value)


def test_is_error_false_with_failure_or_lowconfidence_raises_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Server bug: isError=false should never be paired with non-Success."""
    for bad_type in ("Failure", "LowConfidence"):
        sc = {
            "result_type": bad_type,
            "output_json": None,
            "trace_id": "id",
            "trace_path": None,
            "terminal_results": [],
        }
        bad_result = {"isError": False, "structuredContent": sc, "content": []}
        fake = _FakePopen(_frames(call=_make_call_response(bad_result)))
        _patch_popen_and_binary(monkeypatch, fake)

        with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
            call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
        assert "isError=false" in str(exc_info.value)


# ── invocation outcomes (isError=true) ───────────────────────────────


def test_is_error_true_failure_raises_invocation_error_with_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePopen(
        _frames(
            call=_make_call_response(
                _failure_result(
                    result_type="Failure",
                    trace_id="tid-abc",
                    trace_path="/tmp/tid-abc.jsonl",
                ),
            ),
        ),
    )
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPInvocationError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    exc = exc_info.value
    assert exc.result_type == "Failure"
    assert exc.trace_id == "tid-abc"
    assert exc.trace_path == "/tmp/tid-abc.jsonl"
    assert isinstance(exc.structured_content, dict)


def test_is_error_true_lowconfidence_raises_invocation_error_with_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePopen(
        _frames(
            call=_make_call_response(
                _failure_result(result_type="LowConfidence", trace_id="tid-xyz"),
            ),
        ),
    )
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPInvocationError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert exc_info.value.result_type == "LowConfidence"
    assert exc_info.value.trace_id == "tid-xyz"


# ── JSON-RPC / envelope errors ───────────────────────────────────────


def test_jsonrpc_error_response_raises_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_with_error = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32601, "message": "Method not found"},
    }
    fake = _FakePopen([json.dumps(init_with_error) + "\n"])
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert "JSON-RPC error" in str(exc_info.value)


def test_invalid_json_response_raises_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePopen(["this is not json\n"])
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert "not valid JSON" in str(exc_info.value)


def test_id_mismatch_raises_subprocess_error(monkeypatch: pytest.MonkeyPatch) -> None:
    bad_init = {"jsonrpc": "2.0", "id": 99, "result": {}}
    fake = _FakePopen([json.dumps(bad_init) + "\n"])
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert "id mismatch" in str(exc_info.value)


def test_eof_before_response_raises_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stdout returns '' before any expected response; timer did not
    fire -> NCPSubprocessError ('subprocess closed stdout before
    sending ... response').
    """
    fake = _FakePopen([])
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert "closed stdout" in str(exc_info.value)


class _RaisingReadlineStdout:
    """Test double whose readline() always raises UnicodeDecodeError.

    The runner has a dedicated `except UnicodeDecodeError` clause
    ahead of the generic OSError handler in `_read_response`. This
    fake exercises that code path -- which can fire in practice if
    the subprocess emits non-UTF-8 bytes despite our `encoding="utf-8"`
    pin (rare, but defensive).
    """

    def readline(self) -> str:
        raise UnicodeDecodeError("utf-8", b"\xff\xff\xff", 0, 1, "invalid start byte")


def test_unicode_decode_error_in_response_raises_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stdout.readline() raises UnicodeDecodeError -> NCPSubprocessError
    with a 'failed decoding ... as UTF-8' message.
    """
    fake = _FakePopen(_frames())
    fake.stdout = _RaisingReadlineStdout()  # type: ignore[assignment]
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert "failed decoding" in str(exc_info.value)
    assert "UTF-8" in str(exc_info.value)


# ── subprocess lifecycle ─────────────────────────────────────────────


def test_non_zero_exit_after_dialog_raises_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePopen(_frames(), returncode=2)
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert "exited non-zero" in str(exc_info.value)


def test_graceful_shutdown_stall_without_timer_raises_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """proc.wait() raises TimeoutExpired during graceful shutdown but
    the global timer never fired -> NCPSubprocessError, NOT
    NCPTimeoutError. Protects the locked distinction in
    _close_stdin_and_assert_clean_exit.
    """
    fake = _FakePopen(_frames(), wait_timeout_first=True)
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert "graceful shutdown stalled" in str(exc_info.value)


# ── timer-based timeouts ─────────────────────────────────────────────


def test_timer_fires_during_dialog_raises_timeout_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The global timer fires immediately on start() (before any read
    succeeds). The runner's read sees an empty stdout AND
    timeout_state['fired'] == True -> NCPTimeoutError.
    """
    fake = _FakePopen([])
    _patch_popen_and_binary(monkeypatch, fake)
    monkeypatch.setattr("threading.Timer", _ImmediateTimer)

    with pytest.raises(ncp_langgraph.NCPTimeoutError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert "killed by timer" in str(exc_info.value)
    assert fake.kill_called


def test_timer_fires_during_graceful_shutdown_raises_timeout_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dialog completes successfully, then during graceful shutdown
    wait() raises TimeoutExpired AND timeout_state['fired'] becomes
    True (timer fired mid-wait) -> NCPTimeoutError, NOT
    NCPSubprocessError.
    """
    _CapturingTimer.last_callback = None
    monkeypatch.setattr("threading.Timer", _CapturingTimer)

    def trigger_timer() -> None:
        if _CapturingTimer.last_callback is not None:
            _CapturingTimer.last_callback()

    fake = _FakePopen(_frames(), wait_timeout_first=True, wait_side_effect=trigger_timer)
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPTimeoutError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})
    assert "killed by timer" in str(exc_info.value)
    assert fake.kill_called


# ── binary resolution ────────────────────────────────────────────────


def test_explicit_path_to_directory_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(
            graph="g.yaml",
            brick_dir="b/",
            arguments={},
            binary=str(tmp_path),
        )
    assert "not an executable file" in str(exc_info.value)


def test_missing_bare_name_binary_raises_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ncp_langgraph.subprocess_runner.shutil.which",
        lambda _name: None,
    )
    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(
            graph="g.yaml",
            brick_dir="b/",
            arguments={},
            binary="definitely-not-on-path-12345",
        )
    assert "not found on PATH" in str(exc_info.value)


# ── stderr-log lifecycle ─────────────────────────────────────────────


def _collect_stderr_log_paths(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Capture every stderr log path the runner creates."""
    paths: list[Path] = []
    original = __import__("tempfile").NamedTemporaryFile

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        handle = original(*args, **kwargs)
        paths.append(Path(handle.name))
        return handle

    monkeypatch.setattr("tempfile.NamedTemporaryFile", wrapper)
    return paths


def test_stderr_log_deleted_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _collect_stderr_log_paths(monkeypatch)
    fake = _FakePopen(_frames())
    _patch_popen_and_binary(monkeypatch, fake)

    call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})

    assert len(paths) == 1
    assert not paths[0].exists()


def test_stderr_log_deleted_on_invocation_error(monkeypatch: pytest.MonkeyPatch) -> None:
    paths = _collect_stderr_log_paths(monkeypatch)
    fake = _FakePopen(_frames(call=_make_call_response(_failure_result())))
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPInvocationError):
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})

    assert len(paths) == 1
    assert not paths[0].exists()


def test_stderr_log_deleted_on_ambiguous_tool_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _collect_stderr_log_paths(monkeypatch)
    fake = _FakePopen(
        _frames(
            tools_list=_make_tools_list_response(tools=[{"name": "a"}, {"name": "b"}]),
        ),
    )
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPAmbiguousToolError):
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})

    assert len(paths) == 1
    assert not paths[0].exists()


def test_empty_stderr_log_deleted_on_subprocess_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty stderr logs are deleted regardless of exception type (the
    empty-log override in `_cleanup_stderr_log`). This test pins that
    behavior: subprocess returns non-zero exit, no stderr written, log
    is deleted.
    """
    paths = _collect_stderr_log_paths(monkeypatch)
    fake = _FakePopen(_frames(), returncode=2)
    _patch_popen_and_binary(monkeypatch, fake)

    with pytest.raises(ncp_langgraph.NCPSubprocessError):
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})

    assert len(paths) == 1
    assert not paths[0].exists()


def test_stderr_log_kept_on_subprocess_error_with_nonempty_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-empty stderr log is preserved on NCPSubprocessError so its
    tail is available for diagnostics.
    """
    paths = _collect_stderr_log_paths(monkeypatch)
    fake = _FakePopen(_frames(), returncode=2)
    _patch_popen_and_binary(monkeypatch, fake)

    original_factory = subprocess.Popen  # type: ignore[misc]

    def writing_factory(*args: Any, **kwargs: Any) -> Any:
        proc = original_factory(*args, **kwargs)  # type: ignore[operator]
        stderr_handle = kwargs.get("stderr")
        if hasattr(stderr_handle, "write"):
            stderr_handle.write(b"panic: something went wrong\n")
            stderr_handle.flush()
        return proc

    monkeypatch.setattr("subprocess.Popen", writing_factory)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})

    # Stderr tail must appear in the exception message -- preserving
    # the file on disk is only half the diagnostic plumbing; the user
    # sees the tail through the exception.
    assert "panic: something went wrong" in str(exc_info.value)
    assert len(paths) == 1
    assert paths[0].exists()
    paths[0].unlink()


def test_stderr_log_kept_on_timeout_error_with_nonempty_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-empty stderr log is preserved on NCPTimeoutError so its
    tail is available for diagnostics. Mirrors the subprocess-error
    case but the exception is NCPTimeoutError.
    """
    paths = _collect_stderr_log_paths(monkeypatch)

    _CapturingTimer.last_callback = None
    monkeypatch.setattr("threading.Timer", _CapturingTimer)

    def trigger_timer() -> None:
        if _CapturingTimer.last_callback is not None:
            _CapturingTimer.last_callback()

    fake = _FakePopen(_frames(), wait_timeout_first=True, wait_side_effect=trigger_timer)
    _patch_popen_and_binary(monkeypatch, fake)

    original_factory = subprocess.Popen  # type: ignore[misc]

    def writing_factory(*args: Any, **kwargs: Any) -> Any:
        proc = original_factory(*args, **kwargs)  # type: ignore[operator]
        stderr_handle = kwargs.get("stderr")
        if hasattr(stderr_handle, "write"):
            stderr_handle.write(b"timeout panic\n")
            stderr_handle.flush()
        return proc

    monkeypatch.setattr("subprocess.Popen", writing_factory)

    with pytest.raises(ncp_langgraph.NCPTimeoutError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})

    assert "timeout panic" in str(exc_info.value)
    assert len(paths) == 1
    assert paths[0].exists()
    paths[0].unlink()


def test_popen_oserror_raises_subprocess_error_and_deletes_empty_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spawn-failure path: subprocess.Popen raises OSError. The runner
    wraps it as NCPSubprocessError with a 'failed to spawn' message,
    and the empty stderr log is deleted (empty-log override applies
    even on kept-by-exception-type).
    """
    paths = _collect_stderr_log_paths(monkeypatch)

    def fail_popen(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("boom")

    monkeypatch.setattr(
        "ncp_langgraph.subprocess_runner._resolve_binary",
        lambda _b: Path("/fake/bin"),
    )
    monkeypatch.setattr("subprocess.Popen", fail_popen)

    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        call_ncp_graph(graph="g.yaml", brick_dir="b/", arguments={})

    assert "failed to spawn" in str(exc_info.value)
    assert len(paths) == 1
    assert not paths[0].exists()


# ── integration tests (gated on ``mcp_server_binary`` fixture) ───────


def test_real_echo_pipeline_returns_success(
    mcp_server_binary: Path,
    echo_pipeline_graph: Path,
    brick_dir: Path,
    tmp_path: Path,
) -> None:
    """End-to-end: drive the real binary through the echo pipeline."""
    result = call_ncp_graph(
        graph=echo_pipeline_graph,
        brick_dir=brick_dir,
        arguments={"smoke": "hello"},
        trace_dir=tmp_path,
        binary=mcp_server_binary,
        timeout=30.0,
    )

    assert isinstance(result, ncp_langgraph.RunnerResult)
    assert result.trace["result_type"] == "Success"
    assert isinstance(result.trace["trace_id"], str)
    assert result.trace["trace_id"]


def test_real_trace_file_written_when_trace_dir_set(
    mcp_server_binary: Path,
    echo_pipeline_graph: Path,
    brick_dir: Path,
    tmp_path: Path,
) -> None:
    result = call_ncp_graph(
        graph=echo_pipeline_graph,
        brick_dir=brick_dir,
        arguments={"smoke": "hello"},
        trace_dir=tmp_path,
        binary=mcp_server_binary,
    )

    trace_path = result.trace["trace_path"]
    assert isinstance(trace_path, str)
    p = Path(trace_path)
    assert p.is_file()
    assert p.stat().st_size > 0
    # Consistency with the other two integration tests:
    assert result.trace["trace_id"]


def test_real_no_trace_dir_gives_null_trace_path(
    mcp_server_binary: Path,
    echo_pipeline_graph: Path,
    brick_dir: Path,
) -> None:
    result = call_ncp_graph(
        graph=echo_pipeline_graph,
        brick_dir=brick_dir,
        arguments={"smoke": "hello"},
        binary=mcp_server_binary,
    )

    assert result.trace["trace_path"] is None
    assert isinstance(result.trace["trace_id"], str)
    assert result.trace["trace_id"]
