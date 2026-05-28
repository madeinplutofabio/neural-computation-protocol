<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# `examples/mcp/` — MCP adapter examples

This directory shows how to drop an [NCP] graph into an
MCP-compatible host as a callable tool, using
[`ncp-mcp-server`](https://github.com/madeinplutofabio/neural-computation-protocol/tree/main/crates/ncp-mcp-server)
— the stdio Model Context Protocol adapter for NCP.

**One graph = one MCP tool.** Configure your MCP-compatible host's
tool config to launch `ncp-mcp-server --graph <your-graph>.yaml`,
and that graph becomes a callable tool with no glue code.

[NCP]: https://github.com/madeinplutofabio/neural-computation-protocol

---

## Contents

| File | Purpose |
|---|---|
| `README.md` | This file. |
| [Generic host config snippet](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/examples/mcp/desktop-host-config.example.json) | MCP-compatible-host config snippet adopters customize with their own absolute paths. |
| [Manual echo-pipeline smoke test](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/examples/mcp/echo-pipeline-mcp-smoke.md) | Manual stdio dialog walk-through against the bundled `echo-pipeline` graph. Confirm the adapter works on your host before wiring it into a real MCP-compatible host. |
| [CI smoke script](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/examples/mcp/ci_smoke.py) | The CI smoke script the `mcp-smoke` job runs (Python stdlib only). Adopters can run it locally to verify a build end-to-end. |

---

## Quick start

### Install from crates.io

```bash
cargo install ncp-mcp-server --locked
ncp-mcp-server --version
```

### Source install (fallback)

For pre-release builds or workspace development:

```bash
# 1. Clone the workspace
git clone https://github.com/madeinplutofabio/neural-computation-protocol.git
cd neural-computation-protocol

# 2. Build the adapter binary (release profile)
cargo build -p ncp-mcp-server --release --locked

# 3. The binary is now at:
ls target/release/ncp-mcp-server      # macOS / Linux
ls target/release/ncp-mcp-server.exe  # Windows
```

---

## Wiring into an MCP-compatible host

Many MCP-compatible desktop hosts and MCP-compatible CLI hosts read a
shared-shape JSON config that lists external MCP servers. Copy
[the generic host config snippet](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/examples/mcp/desktop-host-config.example.json)
into your host's config file (location varies by host — consult your
host's docs), and substitute:

- `command`: the **absolute** path to the `ncp-mcp-server` binary you
  built above.
- `args[1]`: the **absolute** path to your graph manifest.
- `args[3]`: the **absolute** path to a directory containing your
  bricks.

The example config wires the bundled `org.ncp-examples.echo-pipeline`
graph; replace the paths to use your own graph.

> Paths in MCP-host configs MUST be absolute. Host working directories
> at launch time vary across hosts; relative paths will silently
> resolve against whatever the host happens to be in.

---

## Verifying the adapter manually

Before wiring into a real MCP-compatible host, confirm the adapter
runs and the JSON-RPC dialog works on your machine — see
[the manual echo-pipeline smoke test](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/examples/mcp/echo-pipeline-mcp-smoke.md)
for a step-by-step recipe.

For an automated end-to-end check, run the same dialog as a Python
script:

```bash
python3 examples/mcp/ci_smoke.py
```

Expected output: `SMOKE OK` to stdout, exit code `0`. The script is
stdlib-only and runs on Linux/macOS/Windows.

---

## How the adapter behaves

- **Stdio transport only** in v0; Streamable HTTP is a future phase.
- **Stdout is reserved for JSON-RPC.** All adapter logs go to stderr
  (MCP spec hard rule).
- **One process per graph configuration** — each `--graph` flag adds
  one tool. Multi-graph supported from day one via repeated `--graph`.
- **Per-call traces** when `--trace-dir <dir>` is set; each
  `tools/call` writes `<dir>/<trace_id>.jsonl`.

Full design reference, including response-shape details and the
Class A / Class B / Class C error mapping:
[Full MCP adapter design doc](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/MCP_ADAPTER.md).

---

## Repo hygiene note

Per the [Full MCP adapter design doc](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/MCP_ADAPTER.md)
§17, this README intentionally avoids specific MCP-host product
names. The config snippet shape is documented by the MCP specification
and used in common by all MCP-compatible hosts the project is aware
of.
