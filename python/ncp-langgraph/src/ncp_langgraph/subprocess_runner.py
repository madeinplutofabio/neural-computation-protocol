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
``tools/call``), closes stdin gracefully, and asserts a clean exit.

Skeleton stage: this is a stub. The real implementation lands in
Phase 3A.3 PR C.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ncp_langgraph.types import RunnerResult


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
            This is the input the NCP graph sees as its root input.
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
            JSON-RPC frames, or a JSON-RPC error response.
        NCPInvocationError: The graph ran to a non-success terminal
            (MCP `result.isError == true`).
        NCPTimeoutError: The configured `timeout` was exceeded.
        NCPAmbiguousToolError: `tools/list` returned multiple tools
            and `tool_name` was not provided.
        NotImplementedError: Always, in the v0.1.0.dev0 skeleton.
            Real implementation lands in Phase 3A.3 PR C.
    """
    raise NotImplementedError(
        "call_ncp_graph is a skeleton stub in ncp-langgraph v0.1.0.dev0. "
        "The real implementation lands in Phase 3A.3 PR C. "
        "See docs/LANGGRAPH_ADAPTER.md §3.3 + §5 for the locked "
        "contract."
    )
