# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Public types: exception hierarchy and `RunnerResult` dataclass.

Per the locked design contract in `docs/LANGGRAPH_ADAPTER.md` (§6 + §7),
these classes are part of the public API and are real types even in the
package skeleton. Only the executable API paths in :mod:`node` and
:mod:`subprocess_runner` raise `NotImplementedError` until PR C and
PR D land the implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class NCPError(Exception):
    """Base class for all `ncp-langgraph` errors.

    Catch this to handle any adapter-raised error without coupling to
    the specific subclass.
    """


class NCPSubprocessError(NCPError):
    """The `ncp-mcp-server` subprocess could not be driven successfully.

    Raised on spawn failure, non-zero exit, malformed JSON-RPC frames,
    or a JSON-RPC error response (MCP transport-level failure, not
    NCP-graph-level failure -- see :class:`NCPInvocationError` for the
    latter).
    """


class NCPInvocationError(NCPError):
    """The NCP graph invocation completed but returned a non-success result.

    Maps to MCP `result.isError == true`. The NCP graph ran to terminal
    `Failure` or `LowConfidence` (or the adapter synthesized a Failure);
    the subprocess itself behaved correctly. This carries trace metadata
    so the caller can inspect what happened without re-running the
    graph.

    Attributes:
        structured_content: The full MCP `structuredContent` object
            returned by `tools/call`.
        result_type: The NCP terminal result type, ``"Failure"`` or
            ``"LowConfidence"``. Mirrors
            `structured_content["result_type"]`.
        trace_id: The NCP trace UUID for this invocation. Present even
            when tracing is disabled.
        trace_path: Absolute path to the JSONL trace file for this
            invocation. `None` when tracing is disabled.
    """

    def __init__(
        self,
        message: str,
        *,
        structured_content: dict[str, Any],
        result_type: str,
        trace_id: str,
        trace_path: str | None,
    ) -> None:
        super().__init__(message)
        self.structured_content: dict[str, Any] = structured_content
        self.result_type: str = result_type
        self.trace_id: str = trace_id
        self.trace_path: str | None = trace_path


class NCPTimeoutError(NCPError):
    """The subprocess did not complete within the configured timeout."""


class NCPAmbiguousToolError(NCPError):
    """`tools/list` returned multiple tools and no `tool_name` was given.

    Raised during invocation when the configured `ncp-mcp-server`
    exposes more than one tool and the caller did not disambiguate via
    `tool_name=...`. With the v0.1.0 one-graph factory this should not
    occur in normal use, but the guard remains for unexpected
    `tools/list` responses and future multi-graph extensions.
    """


@dataclass(frozen=True)
class RunnerResult:
    """Result of a single `ncp-mcp-server` invocation.

    Returned by :func:`call_ncp_graph`. :class:`NCPNode` internally
    maps this into a LangGraph state-update dict; callers who use
    `call_ncp_graph` directly get this dataclass back unchanged.

    The dataclass is frozen to prevent reassignment of its top-level
    fields. Nested JSON values remain normal Python objects.

    Attributes:
        output_json: The NCP graph's terminal output JSON value, taken
            from the MCP `structuredContent.output_json` field.
        structured_content: The full MCP `structuredContent` object, in
            case the caller needs fields beyond `output_json` (e.g.
            `result_type`).
        trace: Trace metadata for this invocation. Keys:
            ``"result_type"`` (str), ``"trace_id"`` (str), and
            ``"trace_path"`` (str or None). `trace_path` is populated
            only when the subprocess was configured with ``--trace-dir``.
    """

    output_json: Any
    structured_content: dict[str, Any]
    trace: dict[str, Any]
