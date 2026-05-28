# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""NCPNode: LangGraph-callable wrapper around `ncp-mcp-server`.

Per the locked design contract in `docs/LANGGRAPH_ADAPTER.md` (§3.1
+ §3.2 + §4), an `NCPNode` instance is callable. Construct via the
locked classmethod factory :meth:`NCPNode.from_subprocess`; register
the instance as a LangGraph node via
``builder.add_node(name, instance)``.

The LangGraph execution surface is ``__call__(state)``, which performs
the locked four-frame MCP dialog against `ncp-mcp-server` (via
:func:`ncp_langgraph.call_ncp_graph` internally) and returns a PARTIAL
state-update dict. It does NOT mutate the input state.

Skeleton stage: :meth:`NCPNode.from_subprocess` and
:meth:`NCPNode.__call__` are stubs. The real implementation lands in
Phase 3A.3 PR D.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class NCPNode:
    """LangGraph-callable wrapper around an `ncp-mcp-server` graph.

    The only supported way to construct an `NCPNode` in v0.1.0 is via
    the :meth:`from_subprocess` classmethod factory. Instances are
    callable so they work directly as LangGraph nodes:

    .. code-block:: python

        node = NCPNode.from_subprocess(graph="...", brick_dir="...")
        builder.add_node("ncp_step", node)

    Each instance binds one graph configuration; construct a separate
    `NCPNode` per graph.
    """

    @classmethod
    def from_subprocess(
        cls,
        graph: str | Path,
        brick_dir: str | Path,
        trace_dir: str | Path | None = None,
        binary: str | Path = "ncp-mcp-server",
        tool_name: str | None = None,
        input_key: str | None = None,
        output_key: str = "ncp_output",
        trace_key: str = "ncp_trace",
        timeout: float = 30.0,
    ) -> NCPNode:
        """Construct an `NCPNode` that drives `ncp-mcp-server` over stdio.

        Each call to the returned node spawns a fresh `ncp-mcp-server`
        subprocess, performs the four-frame MCP dialog
        (``initialize`` -> ``notifications/initialized`` ->
        ``tools/list`` -> ``tools/call``), and returns a partial
        LangGraph state-update dict.

        Args:
            graph: Path to the NCP graph YAML manifest. Forwarded to
                `ncp-mcp-server --graph`.
            brick_dir: Path to the directory containing the graph's
                brick WASM artifacts. Forwarded to
                `ncp-mcp-server --brick-dir`.
            trace_dir: If set, each call writes a per-call JSONL trace
                under this directory. `None` disables tracing.
            binary: Name or absolute path of the `ncp-mcp-server`
                executable. The default ``"ncp-mcp-server"`` resolves
                through `PATH`.
            tool_name: MCP tool name to invoke. If `None`, auto-derived
                from a single-tool ``tools/list`` response.
            input_key: If set, only ``state[input_key]`` is passed to
                MCP ``tools/call`` arguments. If `None`, the whole
                LangGraph state dict is passed verbatim.
            output_key: Key under which
                ``structuredContent.output_json`` is returned in the
                state-update dict. Default ``"ncp_output"``.
            trace_key: Key under which the trace-metadata dict is
                returned in the state-update dict. Default
                ``"ncp_trace"``.
            timeout: Wall-clock timeout in seconds for the full
                invocation (spawn + dialog + close + wait-for-exit).

        Returns:
            A configured :class:`NCPNode` instance ready for
            registration via ``builder.add_node(name, instance)``.

        Raises:
            NotImplementedError: Always, in the v0.1.0.dev0 skeleton.
                Real implementation lands in Phase 3A.3 PR D.
        """
        raise NotImplementedError(
            "NCPNode.from_subprocess is a skeleton stub in "
            "ncp-langgraph v0.1.0.dev0. The real implementation lands "
            "in Phase 3A.3 PR D. See docs/LANGGRAPH_ADAPTER.md §3.1 "
            "for the locked contract."
        )

    def __call__(self, state: dict[str, Any]) -> dict[str, Any]:
        """Invoke the bound NCP graph and return a partial state update.

        Returns a NEW dict containing exactly two keys (the configured
        ``output_key`` and ``trace_key``). Does NOT mutate the input
        ``state``. The LangGraph runtime applies this partial update
        according to the `StateGraph` state schema and reducers.

        Args:
            state: The current LangGraph state dict. Passed verbatim
                as MCP ``tools/call`` arguments unless ``input_key``
                is set, in which case only ``state[input_key]`` is
                sent.

        Returns:
            A new dict
            ``{output_key: <output_json>, trace_key: <trace_metadata>}``
            where ``<trace_metadata>`` has the shape
            ``{"result_type": str, "trace_id": str, "trace_path": str | None}``.

        Raises:
            NCPSubprocessError: Spawn failed, non-zero exit, malformed
                JSON-RPC frames, or a JSON-RPC error response.
            NCPInvocationError: The graph ran to a non-success terminal
                (MCP ``result.isError == true``).
            NCPTimeoutError: The configured ``timeout`` was exceeded.
            NCPAmbiguousToolError: ``tools/list`` returned multiple
                tools and ``tool_name`` was not provided.
            NotImplementedError: Always, in the v0.1.0.dev0 skeleton.
                Real implementation lands in Phase 3A.3 PR D.
        """
        raise NotImplementedError(
            "NCPNode.__call__ is a skeleton stub in "
            "ncp-langgraph v0.1.0.dev0. The real implementation lands "
            "in Phase 3A.3 PR D. See docs/LANGGRAPH_ADAPTER.md §3.2 "
            "+ §4 for the locked contract."
        )
