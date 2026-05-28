<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# ncp-langgraph

> **Pre-release placeholder.** `ncp-langgraph v0.1.0.dev0` is the
> skeleton package for the upcoming v0.1.0 release. The public API
> surface is stubbed but not yet implemented; the executable API paths
> (`NCPNode.from_subprocess(...)`, `NCPNode.__call__(...)`, and
> `call_ncp_graph(...)`) raise `NotImplementedError`. Track progress
> on the
> [Phase 3A.3 milestone](https://github.com/madeinplutofabio/neural-computation-protocol/issues?q=is%3Aissue+label%3Aphase-3.A.3).

LangGraph adapter for the
[Neural Computation Protocol (NCP)](https://github.com/madeinplutofabio/neural-computation-protocol).

`ncp-langgraph` will let a LangGraph `StateGraph` invoke an NCP graph
as a node by spawning the
[`ncp-mcp-server`](https://crates.io/crates/ncp-mcp-server) binary and
talking to it over stdio. One NCP graph = one LangGraph node. No glue
code, no protocol-buffer plumbing, no PyO3 (in v0.1.0).

## Status

- **v0.1.0.dev0** (this release): package skeleton + build/lint/test
  scaffolding. No runnable API.
- **v0.1.0** (next release): `NCPNode.from_subprocess(...)` and
  `call_ncp_graph(...)` shipped against the locked design contract
  in
  [`docs/LANGGRAPH_ADAPTER.md`](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/LANGGRAPH_ADAPTER.md).

## Scope (v0.1.0)

- **Invocation strategy:** subprocess via `ncp-mcp-server` over stdio
  JSON-RPC. No HTTP transport, no PyO3 binding in v0.
- **Sync only.** `NCPAsyncNode` is a v0.2.0+ addition.
- **One subprocess per call.** No persistent pool in v0.1.0 (deferred).
- **One graph per `NCPNode` instance.** Multi-graph adapter instances
  are a v0.2.0+ addition.

For the full design contract, locked signature, state semantics, and
exception model, see
[`docs/LANGGRAPH_ADAPTER.md`](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/LANGGRAPH_ADAPTER.md).

## Install

```bash
pip install ncp-langgraph
```

`ncp-langgraph` requires the `ncp-mcp-server` binary at runtime. Install
it separately from crates.io:

```bash
cargo install ncp-mcp-server --locked
```

The Python package does not bundle the binary because pip cannot ship a
Rust executable.

## Requirements

- Python 3.10+
- `ncp-mcp-server v0.1.x` on `PATH` or an absolute path passed via
  `NCPNode.from_subprocess(binary=...)`
- LangGraph 1.x

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

## Links

- [NCP project](https://github.com/madeinplutofabio/neural-computation-protocol)
- [Design doc](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/LANGGRAPH_ADAPTER.md)
- [MCP adapter (`ncp-mcp-server`)](https://crates.io/crates/ncp-mcp-server)
- [Changelog](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/python/ncp-langgraph/CHANGELOG.md)
- [Issues](https://github.com/madeinplutofabio/neural-computation-protocol/issues)
