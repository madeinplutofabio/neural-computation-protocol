# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""LangGraph adapter for the Neural Computation Protocol (NCP).

`ncp-langgraph` lets a LangGraph `StateGraph` invoke an NCP graph as a
node by spawning the `ncp-mcp-server` binary and talking to it over
stdio.

The locked design contract is documented in
`docs/LANGGRAPH_ADAPTER.md` in the NCP repository.

Public API:

- :class:`NCPNode` -- LangGraph-callable wrapper. Factory:
  :meth:`NCPNode.from_subprocess`.
- :func:`call_ncp_graph` -- lower-level function that returns a
  :class:`RunnerResult` directly, for callers not using LangGraph state.
- :class:`RunnerResult` -- frozen dataclass returned by
  :func:`call_ncp_graph`.
- Exception hierarchy: :class:`NCPError` (base),
  :class:`NCPSubprocessError`, :class:`NCPInvocationError`,
  :class:`NCPTimeoutError`, :class:`NCPAmbiguousToolError`.
"""

from ncp_langgraph.node import NCPNode
from ncp_langgraph.subprocess_runner import call_ncp_graph
from ncp_langgraph.types import (
    NCPAmbiguousToolError,
    NCPError,
    NCPInvocationError,
    NCPSubprocessError,
    NCPTimeoutError,
    RunnerResult,
)

__version__ = "0.1.0.dev0"

__all__ = [
    "NCPNode",
    "call_ncp_graph",
    "RunnerResult",
    "NCPError",
    "NCPSubprocessError",
    "NCPInvocationError",
    "NCPTimeoutError",
    "NCPAmbiguousToolError",
    "__version__",
]
