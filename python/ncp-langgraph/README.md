<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# ncp-langgraph

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![PyPI](https://img.shields.io/pypi/v/ncp-langgraph.svg)](https://pypi.org/project/ncp-langgraph/)

LangGraph adapter for the [Neural Computation Protocol (NCP)](https://github.com/madeinplutofabio/neural-computation-protocol).

`ncp-langgraph` lets you wrap any NCP graph as a LangGraph node:
`NCPNode.from_subprocess(...)` returns a callable instance that spawns
`ncp-mcp-server` over stdio, performs the locked MCP dialog, and
returns a partial state-update dict ready for LangGraph to merge.

**One NCP graph = one LangGraph node.** No glue code, no
protocol-buffer plumbing, no PyO3 (in v0.1.0).

## Quick start

```python
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ncp_langgraph import NCPNode


class State(TypedDict, total=False):
    company_url: str
    qualification: dict[str, Any]
    ncp_trace: dict[str, Any]


qualify_lead = NCPNode.from_subprocess(
    graph="/abs/path/to/lead-qualification.yaml",
    brick_dir="/abs/path/to/bricks",
    output_key="qualification",
    timeout=30.0,
)

builder = StateGraph(State)
builder.add_node("qualify_lead", qualify_lead)
builder.add_edge(START, "qualify_lead")
builder.add_edge("qualify_lead", END)
compiled = builder.compile()

result = compiled.invoke({"company_url": "https://example.com"})

# result["qualification"]  -- the NCP graph's output_json
# result["ncp_trace"]      -- {"result_type", "trace_id", "trace_path"}
```

For a runnable end-to-end example using the bundled echo-pipeline
graph (stub until [issue #29](https://github.com/madeinplutofabio/neural-computation-protocol/issues/29)
ships the real lead-qualification graph), see
[`examples/langgraph/`](https://github.com/madeinplutofabio/neural-computation-protocol/tree/main/examples/langgraph).

## Install

### 1. The NCP MCP adapter binary

```bash
cargo install ncp-mcp-server --version 0.1.0 --locked
```

The version is pinned to keep `ncp-langgraph v0.1.x` reproducible
against a known `ncp-mcp-server` release.

### 2. The Python adapter (this package)

```bash
python -m pip install ncp-langgraph
```

Or pin to a specific version for reproducibility:

```bash
python -m pip install ncp-langgraph==0.1.0
```

`ncp-langgraph` does NOT bundle the `ncp-mcp-server` binary in v0.1.0.
The binary is distributed separately as the Rust crate
`ncp-mcp-server`; install it independently and keep it on `PATH`, or
pass its absolute path via `NCPNode.from_subprocess(binary=...)`.

## Requirements

- Python 3.10+
- `ncp-mcp-server v0.1.x` on `PATH` (or pass an absolute path via
  `NCPNode.from_subprocess(binary=...)`)
- LangGraph 1.x

## v0.1.0 scope and limitations (honest)

- **Sync only.** `NCPNode` exposes a synchronous `__call__`. Native
  async support (`NCPAsyncNode`) is a v0.2.0+ addition.
- **One subprocess per call.** Each invocation spawns a fresh
  `ncp-mcp-server` process. No persistent pool. Negligible cost for
  typical agent workflows; significant for hot-loop / per-token use.
  Persistent pool is a v0.2.0+ perf optimization.
- **One graph per `NCPNode` instance.** Multi-graph adapter instances
  are a v0.2.0+ addition.
- **Subprocess invocation only.** PyO3 direct binding and Streamable
  HTTP transport are deferred (see
  [`docs/LANGGRAPH_ADAPTER.md`](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/LANGGRAPH_ADAPTER.md) §11
  and future-work issue
  [#34](https://github.com/madeinplutofabio/neural-computation-protocol/issues/34)
  for Streamable HTTP).

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

## Links

- [NCP project](https://github.com/madeinplutofabio/neural-computation-protocol)
- [Design doc](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/LANGGRAPH_ADAPTER.md)
- [Examples](https://github.com/madeinplutofabio/neural-computation-protocol/tree/main/examples/langgraph)
- [MCP adapter (`ncp-mcp-server`)](https://crates.io/crates/ncp-mcp-server)
- [Changelog](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/python/ncp-langgraph/CHANGELOG.md)
- [Issues](https://github.com/madeinplutofabio/neural-computation-protocol/issues)
