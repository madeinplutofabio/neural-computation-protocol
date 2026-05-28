# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Tests for :mod:`ncp_langgraph.node` (NCPNode wrapper).

Two layers:

- **Unit tests** with :func:`ncp_langgraph.subprocess_runner.call_ncp_graph`
  mocked via :class:`_MockCallNcpGraph`. Cover constructor behavior,
  state-input/output semantics, key configurability, immutability
  invariant, and exception propagation. Always run.
- **LangGraph integration tests** that build a real
  :class:`langgraph.graph.StateGraph`, register the ``NCPNode``
  instance, compile, and invoke end-to-end. One uses the mock runner;
  one is gated on the real ``ncp-mcp-server`` binary via the
  ``mcp_server_binary`` fixture.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, TypedDict

import pytest

import ncp_langgraph
from ncp_langgraph.node import NCPNode
from ncp_langgraph.types import RunnerResult

# ── helpers ───────────────────────────────────────────────────────────


def _make_runner_result(
    output_json: Any = None,
    result_type: str = "Success",
    trace_id: str = "trace-uuid",
    trace_path: str | None = None,
) -> RunnerResult:
    """Build a `RunnerResult` for use as a mock return value."""
    if output_json is None:
        output_json = {"echoed": "ok"}
    sc: dict[str, Any] = {
        "result_type": result_type,
        "output_json": output_json,
        "trace_id": trace_id,
        "trace_path": trace_path,
        "terminal_results": [],
    }
    return RunnerResult(
        output_json=output_json,
        structured_content=sc,
        trace={
            "result_type": result_type,
            "trace_id": trace_id,
            "trace_path": trace_path,
        },
    )


class _MockCallNcpGraph:
    """Test double for `call_ncp_graph`. Records every call's args.

    Configure with ``return_value`` (a ``RunnerResult``) for the happy
    path, ``raise_exception`` (any exception instance) to make the
    mock raise instead of returning, or ``mutate_arguments=True`` to
    make the mock mutate the arguments dict it receives -- used to
    prove NCPNode's defensive-copy invariant locks state immutability.
    """

    def __init__(
        self,
        return_value: RunnerResult | None = None,
        raise_exception: BaseException | None = None,
        mutate_arguments: bool = False,
    ) -> None:
        self.return_value = return_value if return_value is not None else _make_runner_result()
        self.raise_exception = raise_exception
        self.mutate_arguments = mutate_arguments
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        graph: str | Path,
        brick_dir: str | Path,
        arguments: dict[str, Any],
        *,
        trace_dir: str | Path | None = None,
        binary: str | Path = "ncp-mcp-server",
        tool_name: str | None = None,
        timeout: float = 30.0,
    ) -> RunnerResult:
        if self.mutate_arguments and isinstance(arguments, dict):
            arguments["_runner_mutated"] = "yes"
        self.calls.append(
            {
                "graph": graph,
                "brick_dir": brick_dir,
                "arguments": arguments,
                "trace_dir": trace_dir,
                "binary": binary,
                "tool_name": tool_name,
                "timeout": timeout,
            }
        )
        if self.raise_exception is not None:
            raise self.raise_exception
        return self.return_value


def _install_mock(monkeypatch: pytest.MonkeyPatch, mock: _MockCallNcpGraph) -> None:
    """Patch the `call_ncp_graph` symbol that `node.py` imported."""
    monkeypatch.setattr("ncp_langgraph.node.call_ncp_graph", mock)


# ── constructor / factory ─────────────────────────────────────────────


def test_from_subprocess_returns_ncpnode_instance() -> None:
    node = NCPNode.from_subprocess(graph="g.yaml", brick_dir="b/")
    assert isinstance(node, NCPNode)


def test_from_subprocess_stores_all_config(monkeypatch: pytest.MonkeyPatch) -> None:
    mock = _MockCallNcpGraph()
    _install_mock(monkeypatch, mock)

    node = NCPNode.from_subprocess(
        graph="my-graph.yaml",
        brick_dir="my-bricks/",
        trace_dir="/tmp/traces",
        binary="/abs/path/ncp-mcp-server",
        tool_name="my-tool",
        input_key="payload",
        output_key="result",
        trace_key="trace",
        timeout=15.0,
    )
    node({"payload": {"x": 1}})

    assert len(mock.calls) == 1
    c = mock.calls[0]
    assert c["graph"] == "my-graph.yaml"
    assert c["brick_dir"] == "my-bricks/"
    assert c["trace_dir"] == "/tmp/traces"
    assert c["binary"] == "/abs/path/ncp-mcp-server"
    assert c["tool_name"] == "my-tool"
    assert c["timeout"] == 15.0


def test_default_output_key_and_trace_key(monkeypatch: pytest.MonkeyPatch) -> None:
    mock = _MockCallNcpGraph()
    _install_mock(monkeypatch, mock)

    node = NCPNode.from_subprocess(graph="g.yaml", brick_dir="b/")
    update = node({"x": 1})

    assert "ncp_output" in update
    assert "ncp_trace" in update


def test_output_key_and_trace_key_must_be_distinct() -> None:
    """Equal output_key and trace_key would silently collapse the
    returned dict to one key (locked design doc §4.1 says exactly two).
    Construction must fail loudly.
    """
    with pytest.raises(ValueError) as exc_info:
        NCPNode.from_subprocess(
            graph="g.yaml",
            brick_dir="b/",
            output_key="same",
            trace_key="same",
        )
    assert "must be different" in str(exc_info.value)


# ── state input semantics ────────────────────────────────────────────


def test_whole_state_passed_when_input_key_none(monkeypatch: pytest.MonkeyPatch) -> None:
    mock = _MockCallNcpGraph()
    _install_mock(monkeypatch, mock)

    node = NCPNode.from_subprocess(graph="g.yaml", brick_dir="b/")
    state = {"a": 1, "b": "two", "c": [3]}
    node(state)

    assert len(mock.calls) == 1
    # Whole state passed with same content (input_key=None default);
    # the runner sees a deep-copy of state, not the state object itself.
    assert mock.calls[0]["arguments"] == {"a": 1, "b": "two", "c": [3]}


def test_input_key_narrows_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    mock = _MockCallNcpGraph()
    _install_mock(monkeypatch, mock)

    node = NCPNode.from_subprocess(graph="g.yaml", brick_dir="b/", input_key="payload")
    state = {"payload": {"target": "alice"}, "other_field": "ignored"}
    node(state)

    assert len(mock.calls) == 1
    # Only state["payload"] passed, not the whole state
    assert mock.calls[0]["arguments"] == {"target": "alice"}


def test_missing_input_key_raises_keyerror(monkeypatch: pytest.MonkeyPatch) -> None:
    """When input_key is set but missing from state, KeyError surfaces
    naturally. This is a state-schema bug; we don't wrap it because
    Python's natural KeyError is clear and adopters will recognize it.
    """
    mock = _MockCallNcpGraph()
    _install_mock(monkeypatch, mock)

    node = NCPNode.from_subprocess(graph="g.yaml", brick_dir="b/", input_key="payload")

    with pytest.raises(KeyError):
        node({"other_field": "no payload here"})
    # The runner was NOT called -- error fires before invocation
    assert len(mock.calls) == 0


# ── state output / key configurability ───────────────────────────────


def test_call_returns_partial_state_update_dict_with_two_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock = _MockCallNcpGraph()
    _install_mock(monkeypatch, mock)

    node = NCPNode.from_subprocess(graph="g.yaml", brick_dir="b/")
    update = node({"x": 1})

    # Exactly two keys (locked design doc §4.1)
    assert set(update.keys()) == {"ncp_output", "ncp_trace"}


def test_output_json_returned_under_output_key(monkeypatch: pytest.MonkeyPatch) -> None:
    mock = _MockCallNcpGraph(
        return_value=_make_runner_result(output_json={"qualified": True, "score": 82})
    )
    _install_mock(monkeypatch, mock)

    node = NCPNode.from_subprocess(graph="g.yaml", brick_dir="b/")
    update = node({"x": 1})

    assert update["ncp_output"] == {"qualified": True, "score": 82}


def test_trace_metadata_returned_under_trace_key(monkeypatch: pytest.MonkeyPatch) -> None:
    mock = _MockCallNcpGraph(
        return_value=_make_runner_result(trace_id="tid-abc", trace_path="/tmp/abc.jsonl")
    )
    _install_mock(monkeypatch, mock)

    node = NCPNode.from_subprocess(graph="g.yaml", brick_dir="b/")
    update = node({"x": 1})

    assert update["ncp_trace"] == {
        "result_type": "Success",
        "trace_id": "tid-abc",
        "trace_path": "/tmp/abc.jsonl",
    }


def test_custom_output_key(monkeypatch: pytest.MonkeyPatch) -> None:
    mock = _MockCallNcpGraph()
    _install_mock(monkeypatch, mock)

    node = NCPNode.from_subprocess(graph="g.yaml", brick_dir="b/", output_key="qualification")
    update = node({"x": 1})

    assert "qualification" in update
    assert "ncp_output" not in update


def test_custom_trace_key(monkeypatch: pytest.MonkeyPatch) -> None:
    mock = _MockCallNcpGraph()
    _install_mock(monkeypatch, mock)

    node = NCPNode.from_subprocess(graph="g.yaml", brick_dir="b/", trace_key="audit")
    update = node({"x": 1})

    assert "audit" in update
    assert "ncp_trace" not in update


# ── state immutability (locked design doc §4.4) ──────────────────────


def test_state_dict_not_mutated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Input state dict equals its deep copy after the call."""
    mock = _MockCallNcpGraph()
    _install_mock(monkeypatch, mock)

    node = NCPNode.from_subprocess(graph="g.yaml", brick_dir="b/")
    state = {"input": "hello", "x": 1, "y": [1, 2, 3], "nested": {"a": "b"}}
    state_before = copy.deepcopy(state)

    node(state)

    assert state == state_before


def test_state_immutability_with_input_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """State immutability holds even when input_key narrows arguments."""
    mock = _MockCallNcpGraph()
    _install_mock(monkeypatch, mock)

    node = NCPNode.from_subprocess(graph="g.yaml", brick_dir="b/", input_key="payload")
    state = {"payload": {"target": "alice"}, "other": "data"}
    state_before = copy.deepcopy(state)

    node(state)

    assert state == state_before


def test_returned_dict_is_new_object(monkeypatch: pytest.MonkeyPatch) -> None:
    """The returned partial-update dict is a NEW dict, not the input."""
    mock = _MockCallNcpGraph()
    _install_mock(monkeypatch, mock)

    node = NCPNode.from_subprocess(graph="g.yaml", brick_dir="b/")
    state = {"input": "hello"}
    update = node(state)

    assert update is not state
    # The update contains only the configured keys, NOT the input keys
    assert "input" not in update


def test_state_not_mutated_when_mock_mutates_arguments_whole_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NCPNode's defensive deep-copy protects state even when the
    runner (mocked here) mutates the arguments dict it received.
    Whole-state pass-through mode (input_key=None).
    """
    mock = _MockCallNcpGraph(mutate_arguments=True)
    _install_mock(monkeypatch, mock)

    node = NCPNode.from_subprocess(graph="g.yaml", brick_dir="b/")
    state = {"input": "hello", "nested": {"k": "v"}}
    state_before = copy.deepcopy(state)

    node(state)

    # Mock did mutate the arguments it received...
    assert mock.calls[0]["arguments"].get("_runner_mutated") == "yes"
    # ...but state is untouched because NCPNode deep-copied first.
    assert state == state_before
    assert "_runner_mutated" not in state


def test_state_not_mutated_when_mock_mutates_arguments_with_input_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NCPNode's defensive deep-copy protects state even when the
    runner mutates arguments. Narrow-input mode (input_key="payload").
    The nested dict at state["payload"] must also be untouched.
    """
    mock = _MockCallNcpGraph(mutate_arguments=True)
    _install_mock(monkeypatch, mock)

    node = NCPNode.from_subprocess(graph="g.yaml", brick_dir="b/", input_key="payload")
    state = {"payload": {"target": "alice"}, "other": "data"}
    state_before = copy.deepcopy(state)

    node(state)

    assert mock.calls[0]["arguments"].get("_runner_mutated") == "yes"
    assert state == state_before
    # Crucially, the nested dict at state["payload"] is untouched:
    assert "_runner_mutated" not in state["payload"]


# ── exception propagation ────────────────────────────────────────────


def test_invocation_error_propagates_with_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    err = ncp_langgraph.NCPInvocationError(
        "graph failed",
        structured_content={"result_type": "Failure"},
        result_type="Failure",
        trace_id="tid-x",
        trace_path="/tmp/tid-x.jsonl",
    )
    mock = _MockCallNcpGraph(raise_exception=err)
    _install_mock(monkeypatch, mock)

    node = NCPNode.from_subprocess(graph="g.yaml", brick_dir="b/")

    with pytest.raises(ncp_langgraph.NCPInvocationError) as exc_info:
        node({"x": 1})
    # Identity: the EXACT exception object propagates, not a
    # re-raised copy. pytest.raises(SomeError) only checks isinstance;
    # `is` proves verbatim propagation per design doc §6.
    assert exc_info.value is err
    assert exc_info.value.result_type == "Failure"
    assert exc_info.value.trace_id == "tid-x"
    assert exc_info.value.trace_path == "/tmp/tid-x.jsonl"


def test_subprocess_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    err = ncp_langgraph.NCPSubprocessError("boom")
    mock = _MockCallNcpGraph(raise_exception=err)
    _install_mock(monkeypatch, mock)

    node = NCPNode.from_subprocess(graph="g.yaml", brick_dir="b/")
    with pytest.raises(ncp_langgraph.NCPSubprocessError) as exc_info:
        node({"x": 1})
    assert exc_info.value is err


def test_timeout_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    err = ncp_langgraph.NCPTimeoutError("late")
    mock = _MockCallNcpGraph(raise_exception=err)
    _install_mock(monkeypatch, mock)

    node = NCPNode.from_subprocess(graph="g.yaml", brick_dir="b/")
    with pytest.raises(ncp_langgraph.NCPTimeoutError) as exc_info:
        node({"x": 1})
    assert exc_info.value is err


def test_ambiguous_tool_error_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    err = ncp_langgraph.NCPAmbiguousToolError("pick one")
    mock = _MockCallNcpGraph(raise_exception=err)
    _install_mock(monkeypatch, mock)

    node = NCPNode.from_subprocess(graph="g.yaml", brick_dir="b/")
    with pytest.raises(ncp_langgraph.NCPAmbiguousToolError) as exc_info:
        node({"x": 1})
    assert exc_info.value is err


# ── LangGraph round-trip (mocked) — REQUIRED ─────────────────────────


class _MockedRoundTripState(TypedDict, total=False):
    """State schema for the mocked LangGraph round-trip test.

    `total=False` allows the initial state to omit `ncp_output` and
    `ncp_trace`; they're populated by the NCPNode's partial update.
    """

    input_text: str
    ncp_output: dict[str, Any]
    ncp_trace: dict[str, Any]


def test_langgraph_stategraph_round_trip_with_mock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Build a real `langgraph.graph.StateGraph`, register the NCPNode,
    compile, invoke. Asserts that the partial-update dict the node
    returns is merged into the LangGraph state correctly.

    This is the locked "sync callable inside LangGraph" smoke test.
    """
    from langgraph.graph import END, START, StateGraph

    mock = _MockCallNcpGraph(
        return_value=_make_runner_result(
            output_json={"shaped": "by-mock"},
            trace_id="rt-test",
            trace_path="/tmp/rt-test.jsonl",
        ),
    )
    _install_mock(monkeypatch, mock)

    node = NCPNode.from_subprocess(graph="g.yaml", brick_dir="b/")

    builder: StateGraph = StateGraph(_MockedRoundTripState)
    builder.add_node("ncp_step", node)
    builder.add_edge(START, "ncp_step")
    builder.add_edge("ncp_step", END)
    compiled = builder.compile()

    final_state = compiled.invoke({"input_text": "hello"})

    # The merged state contains both the original input field AND the
    # NCPNode's partial-update fields:
    assert final_state["input_text"] == "hello"
    assert final_state["ncp_output"] == {"shaped": "by-mock"}
    assert final_state["ncp_trace"]["result_type"] == "Success"
    assert final_state["ncp_trace"]["trace_id"] == "rt-test"

    # The mock saw the whole state as the MCP arguments (input_key
    # default = None):
    assert len(mock.calls) == 1
    assert mock.calls[0]["arguments"] == {"input_text": "hello"}


# ── real-binary LangGraph integration (gated) ────────────────────────


class _RealRoundTripState(TypedDict, total=False):
    smoke: str
    ncp_output: dict[str, Any]
    ncp_trace: dict[str, Any]


def test_real_langgraph_stategraph_round_trip(
    mcp_server_binary: Path,
    echo_pipeline_graph: Path,
    brick_dir: Path,
    tmp_path: Path,
) -> None:
    """End-to-end through LangGraph against the real `ncp-mcp-server`
    binary + echo-pipeline graph. Proves the "drop NCPNode into your
    StateGraph" claim end-to-end on this machine.
    """
    from langgraph.graph import END, START, StateGraph

    node = NCPNode.from_subprocess(
        graph=echo_pipeline_graph,
        brick_dir=brick_dir,
        trace_dir=tmp_path,
        binary=mcp_server_binary,
        timeout=30.0,
    )

    builder: StateGraph = StateGraph(_RealRoundTripState)
    builder.add_node("ncp_step", node)
    builder.add_edge(START, "ncp_step")
    builder.add_edge("ncp_step", END)
    compiled = builder.compile()

    final_state = compiled.invoke({"smoke": "hello"})

    assert final_state["smoke"] == "hello"
    assert "ncp_output" in final_state
    assert "ncp_trace" in final_state
    assert final_state["ncp_trace"]["result_type"] == "Success"
    assert isinstance(final_state["ncp_trace"]["trace_id"], str)
    assert final_state["ncp_trace"]["trace_id"]
