# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Skeleton smoke test for ncp-langgraph v0.1.0.dev0.

Proves the import contract: the package imports cleanly, exposes
``__version__``, exports every symbol listed in ``__all__``, and the
non-executable public types (``RunnerResult`` and the exception
hierarchy) are constructable / raisable as real Python objects.

This file does NOT test the executable API paths
(``NCPNode.from_subprocess``, ``NCPNode.__call__``,
``call_ncp_graph``); those raise ``NotImplementedError`` in the
skeleton, and the assertions that they raise (and later, that they
work) belong with the PR C and PR D implementation tests.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, is_dataclass

import pytest

import ncp_langgraph


def test_package_imports() -> None:
    """`import ncp_langgraph` succeeds and exposes a non-empty version."""
    assert isinstance(ncp_langgraph.__version__, str)
    assert ncp_langgraph.__version__ != ""


def test_version_matches_package_release() -> None:
    """``__version__`` matches the locked v0.1.0 package release string
    AND matches the installed package metadata version.

    Regression sentinel that catches drift between the three version
    sources: ``pyproject.toml`` (wheel metadata),
    ``src/ncp_langgraph/_version.py`` (runtime ``__version__``), and
    the value this assertion pins. The metadata-parity assertion in
    particular catches the pre-publish ceremony's biggest risk: a
    wheel whose runtime ``__version__`` disagrees with the version
    encoded in its own metadata. CI runs this every push.
    """
    import importlib.metadata as md

    assert ncp_langgraph.__version__ == "0.1.0"
    assert md.version("ncp-langgraph") == ncp_langgraph.__version__


def test_public_surface_exports() -> None:
    """Every symbol in ``__all__`` is importable from the package root."""
    for name in ncp_langgraph.__all__:
        assert hasattr(ncp_langgraph, name), f"{name!r} listed in __all__ but missing"


def test_runner_result_is_constructable() -> None:
    """``RunnerResult`` is a real frozen dataclass, not a stub."""
    assert is_dataclass(ncp_langgraph.RunnerResult)

    result = ncp_langgraph.RunnerResult(
        output_json={"ok": True},
        structured_content={"output_json": {"ok": True}, "result_type": "Success"},
        trace={"result_type": "Success", "trace_id": "test", "trace_path": None},
    )
    assert result.output_json == {"ok": True}
    assert result.trace["trace_id"] == "test"

    with pytest.raises(FrozenInstanceError):
        result.output_json = {"ok": False}


def test_exception_hierarchy() -> None:
    """All four typed exceptions inherit from ``NCPError`` (and ``Exception``)."""
    for exc_cls in (
        ncp_langgraph.NCPSubprocessError,
        ncp_langgraph.NCPInvocationError,
        ncp_langgraph.NCPTimeoutError,
        ncp_langgraph.NCPAmbiguousToolError,
    ):
        assert issubclass(exc_cls, ncp_langgraph.NCPError)
        assert issubclass(exc_cls, Exception)


def test_ncp_invocation_error_carries_trace_metadata() -> None:
    """``NCPInvocationError`` carries the locked trace-metadata attributes."""
    exc = ncp_langgraph.NCPInvocationError(
        "graph returned Failure",
        structured_content={"result_type": "Failure"},
        result_type="Failure",
        trace_id="abc-123",
        trace_path="/tmp/abc-123.jsonl",
    )
    assert exc.result_type == "Failure"
    assert exc.trace_id == "abc-123"
    assert exc.trace_path == "/tmp/abc-123.jsonl"
    assert exc.structured_content == {"result_type": "Failure"}
