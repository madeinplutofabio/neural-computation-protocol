<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# ncp-mcp-server

<p>
  <a href="https://crates.io/crates/ncp-mcp-server"><img alt="crates.io" src="https://img.shields.io/crates/v/ncp-mcp-server?logo=rust&label=crates.io" /></a>&nbsp;<a href="https://docs.rs/ncp-mcp-server"><img alt="docs.rs" src="https://img.shields.io/docsrs/ncp-mcp-server" /></a>&nbsp;<a href="https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/rust-toolchain.toml"><img alt="MSRV" src="https://img.shields.io/crates/msrv/ncp-mcp-server" /></a>&nbsp;<a href="https://opensource.org/licenses/Apache-2.0"><img alt="License" src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" /></a>
</p>

A stdio [Model Context Protocol](https://modelcontextprotocol.io) adapter for
[NCP](https://github.com/madeinplutofabio/neural-computation-protocol) —
exposes auditable WASM Brick graphs as MCP tools that any MCP-compatible host
can call.

**One graph = one MCP tool.** Configure your MCP-compatible host to launch
`ncp-mcp-server --graph graph.yaml`, and that graph becomes a callable tool.
No glue code, no protocol translation in your app.

## Install

```bash
cargo install ncp-mcp-server --locked
ncp-mcp-server --version
```

Source install fallback (latest unreleased main):

```bash
git clone https://github.com/madeinplutofabio/neural-computation-protocol.git
cd neural-computation-protocol
cargo install --path crates/ncp-mcp-server --locked
```

Requires Rust **1.94+**.

## Wiring into an MCP-compatible host

MCP-compatible desktop hosts and MCP-compatible CLI hosts typically read a
JSON config that lists external MCP servers. The shape is documented by the
MCP specification and used in common across hosts:

```json
{
  "mcpServers": {
    "ncp-echo-pipeline": {
      "command": "/absolute/path/to/ncp-mcp-server",
      "args": [
        "--graph",
        "/absolute/path/to/your/graph.yaml",
        "--brick-dir",
        "/absolute/path/to/your/bricks"
      ]
    }
  }
}
```

Paths MUST be absolute. The host's working directory at launch time varies
across hosts.

For a ready-to-customize example, a manual stdio dialog smoke recipe, and
the CI smoke script that gates every PR, see the
[MCP example directory](https://github.com/madeinplutofabio/neural-computation-protocol/tree/main/examples/mcp).

## Quick example (multi-graph)

```bash
ncp-mcp-server \
  --graph path/to/triage-graph.yaml \
  --graph path/to/extract-graph.yaml \
  --brick-dir path/to/bricks \
  --trace-dir /tmp/ncp-mcp-traces
```

Each graph becomes one MCP tool with a name derived from the graph's
`graph_id`. JSON-RPC traffic flows on stdin/stdout; diagnostics go to stderr;
per-call execution traces, when `--trace-dir` is set, land in
`<dir>/<trace_id>.jsonl`.

## Documentation

- [Design doc](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/MCP_ADAPTER.md) — binding architectural decisions for this adapter
- [Examples](https://github.com/madeinplutofabio/neural-computation-protocol/tree/main/examples/mcp) — host config snippet, manual smoke recipe, CI smoke script
- [NCP project README](https://github.com/madeinplutofabio/neural-computation-protocol) — what NCP is and why
- [NCP install guide](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/INSTALL.md) — runtime + adapter install paths
- [NCP adoption guide](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/ADOPTION_GUIDE.md) — how to use NCP in your stack
- [NCP protocol spec](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/spec/ncp-v0.2.3.md) — the NCP protocol this adapter exposes
- [MCP spec](https://modelcontextprotocol.io/specification/2025-11-25) — the protocol this adapter implements

## Status

`v0.1.0` targets MCP spec **2025-11-25** over **stdio transport only**.
Streamable HTTP transport is deferred to a future release.

The CLI is the stable interface. The Rust module API is treated as
reference-implementation internals until adopter feedback shapes a stable
public surface.

## License

Apache-2.0 — see [LICENSE](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/LICENSE) and [NOTICE](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/NOTICE).
