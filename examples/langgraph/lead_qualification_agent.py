#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""LangGraph lead-qualification example using ncp-langgraph.

This example demonstrates the canonical adopter integration pattern:

1. Build a LangGraph ``StateGraph`` for your workflow shape.
2. Wrap an NCP graph as an ``NCPNode`` via
   :meth:`NCPNode.from_subprocess`.
3. Register the ``NCPNode`` in the ``StateGraph`` via
   ``builder.add_node(name, instance)``.
4. Compile + invoke.

UNDERLYING NCP GRAPH (current stub vs intended)
================================================
This example WIRES the echo-pipeline graph (a passthrough NCP graph
bundled with the workspace) as the underlying NCP graph. Echo just
mirrors its input back, so the ``qualification`` output you'll see is
NOT a real qualification verdict -- it's the state you sent in.

# TODO(#29): Replace echo-pipeline with the lead-qualification graph
# once examples/graphs/lead-qualification/ lands.

Tracking the real lead-qualification graph: issue #29
(https://github.com/madeinplutofabio/neural-computation-protocol/issues/29).

This substitution decouples Phase 3A.3 (the adapter) from issue #29
(the graph) so the adapter can ship without blocking on graph-authoring
work.

REQUIREMENTS
============
- Run from a clone of the NCP workspace. The example resolves the
  echo-pipeline graph + bricks via relative paths from this file.
- ``cargo install ncp-mcp-server --version 0.1.0 --locked`` (or build
  from source).
- ``pip install -e python/ncp-langgraph`` (or
  ``pip install ncp-langgraph`` once published to PyPI in PR F).
- See ``examples/langgraph/requirements.txt`` for the Python deps.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from ncp_langgraph import NCPNode

# ── workspace paths ──────────────────────────────────────────────────


_WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
_ECHO_GRAPH = _WORKSPACE_ROOT / "examples" / "graphs" / "echo-pipeline" / "graph.yaml"
_BRICK_DIR = _WORKSPACE_ROOT / "examples" / "bricks"


# ── LangGraph state schema ───────────────────────────────────────────


class LeadState(TypedDict, total=False):
    """State carried across the LangGraph workflow.

    ``qualification`` and ``ncp_trace`` are populated by the NCPNode's
    partial state update (LangGraph merges them in via the default
    last-write-wins reducer).
    """

    company_url: str
    target_icp: str
    qualification: dict[str, Any]
    ncp_trace: dict[str, Any]


# ── build the workflow ───────────────────────────────────────────────


def build_lead_qualifier() -> Any:
    """Build and compile the LangGraph workflow.

    Returns the compiled graph (a Runnable) ready to ``.invoke(state)``.
    """
    qualify_lead = NCPNode.from_subprocess(
        graph=_ECHO_GRAPH,
        brick_dir=_BRICK_DIR,
        # Surface the NCP graph's output under a domain-meaningful
        # key. Echo just mirrors input, so while we're stubbed on
        # echo-pipeline this looks like state echo; once the real
        # lead-qualification graph lands (#29) it'll carry icp_match,
        # score, reasons, etc.
        output_key="qualification",
        timeout=30.0,
    )

    builder: StateGraph = StateGraph(LeadState)
    builder.add_node("qualify_lead", qualify_lead)
    builder.add_edge(START, "qualify_lead")
    builder.add_edge("qualify_lead", END)
    return builder.compile()


# ── run end-to-end ───────────────────────────────────────────────────


def main() -> None:
    # Sanity-check workspace assets before invoking. Gives a clear
    # error rather than a confusing NCP subprocess failure when the
    # user runs the example outside a workspace clone.
    if not _ECHO_GRAPH.is_file():
        sys.exit(
            f"Example asset missing: {_ECHO_GRAPH}\n"
            f"Run this example from a clone of the NCP workspace; the "
            f"echo-pipeline graph and bricks are bundled at "
            f"examples/graphs/echo-pipeline/ and examples/bricks/."
        )
    if not _BRICK_DIR.is_dir():
        sys.exit(
            f"Example asset missing: {_BRICK_DIR}\n"
            f"Run this example from a clone of the NCP workspace; the "
            f"echo-pipeline graph and bricks are bundled at "
            f"examples/graphs/echo-pipeline/ and examples/bricks/."
        )

    workflow = build_lead_qualifier()

    initial_state: LeadState = {
        "company_url": "https://example.com",
        "target_icp": "B2B SaaS selling to creators",
    }

    print("=== Invoking lead-qualification workflow ===")
    print(f"Initial state: {dict(initial_state)}")
    print()

    final_state = workflow.invoke(initial_state)

    print("=== Final state ===")
    for key, value in final_state.items():
        print(f"  {key}: {value}")
    print()

    # While stubbed on echo-pipeline, qualification mirrors the input
    # state. When the real graph lands (#29) it carries the actual
    # qualification verdict.
    print("=== NOTE ===")
    print(
        "qualification above mirrors the input state because this "
        "example is currently stubbed on the echo-pipeline graph. "
        "See module docstring TODO(#29) for the planned replacement."
    )


if __name__ == "__main__":
    main()
