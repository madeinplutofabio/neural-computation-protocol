<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

<p align="center">
  <img src="https://github.com/madeinplutofabio/neural-computation-protocol/raw/main/NCP-logo.png" alt="NCP Logo" width="200" />
</p>

<h1 align="center">NCP: Neural Computation Protocol</h1>

<p align="center">
  <strong>Faster AI agents. Fewer tokens. Reusable workflows.</strong>
</p>

<p align="center">
  <a href="https://github.com/madeinplutofabio/neural-computation-protocol/actions/workflows/validate.yml"><img alt="CI" src="https://github.com/madeinplutofabio/neural-computation-protocol/actions/workflows/validate.yml/badge.svg" /></a>&nbsp;<a href="https://crates.io/crates/ncp-runtime"><img alt="ncp-runtime on crates.io" src="https://img.shields.io/crates/v/ncp-runtime?logo=rust&label=ncp-runtime" /></a>&nbsp;<a href="https://crates.io/crates/ncp-mcp-server"><img alt="ncp-mcp-server on crates.io" src="https://img.shields.io/crates/v/ncp-mcp-server?logo=rust&label=ncp-mcp-server" /></a>&nbsp;<a href="https://pypi.org/project/ncp-runtime/"><img alt="ncp-runtime on PyPI" src="https://img.shields.io/pypi/v/ncp-runtime?logo=python&label=ncp-runtime" /></a>&nbsp;<a href="https://pypi.org/project/ncp-langgraph/"><img alt="ncp-langgraph on PyPI" src="https://img.shields.io/pypi/v/ncp-langgraph?logo=python&label=ncp-langgraph" /></a>&nbsp;<a href="https://opensource.org/licenses/Apache-2.0"><img alt="License" src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" /></a>
</p>

---

Not everything an AI agent does needs an AI model.

Many agents call a language model for routine work that ordinary software could handle faster, more cheaply, and with more predictable results. NCP helps agents avoid those unnecessary model calls, without making developers rebuild the same logic in every application.

## Why NCP?

- **Lower AI costs.** Fewer model calls, fewer tokens.
- **Faster responses.** Routine work finishes without waiting for inference.
- **More predictable outcomes.** Known rules produce consistent results.
- **Build once, reuse anywhere.** Package useful workflows and share them across applications and agent frameworks.
- **Greater control.** Execute workflows locally, with explicit limits and traceable results.

## Why not just use Python?

You can, and sometimes you should. If one application needs a handful of rules, ordinary Python functions are enough.

NCP becomes valuable when the same capability is needed by several agents, by agents that use different frameworks, or by another team that wants to reuse your logic without copying code, rebuilding dependencies, or maintaining separate integrations. Then it helps to package the logic once, distribute it, install specific versions, and run it the same way from Python, from MCP, and from LangGraph.

## Try it (60 seconds)

```bash
pip install 'ncp-runtime[workflows]'
python -m ncp_runtime.demo
```

Resolves the bundled `ncp` runtime binary, runs a tiny built-in graph, and prints `result_type` plus a one-line summary per terminal. Exit 0 on `Success`.

A richer, two-Brick example (sales-lead qualification using only deterministic WASM computations, no LLM) lives at [`examples/graphs/lead-qualification/`](examples/graphs/lead-qualification/).

## Run a reusable workflow from your framework

Install a published workflow archive, then run it by name from whichever host you already use. The MCP server and the LangGraph adapter are unchanged; the SDK's `workflows` subcommand just supplies them with the right paths.

### Python

```python
from ncp_runtime import NCP

result = NCP().run_workflow(
    "com.my-vendor.my-workflow",
    inputs={"text": "hello"},
    version="0.1.0",
)
print(result.terminals[0].output)
```

### MCP-compatible host

```bash
python -m ncp_runtime.workflows mcp-config \
  com.my-vendor.my-workflow --version 0.1.0 --format snippet
```

Paste the printed `mcpServers` block into your MCP host's config.

### LangGraph

```python
from ncp_runtime.workflows import resolve
from ncp_langgraph import NCPNode

graph, brick_dir = resolve("com.my-vendor.my-workflow", version="0.1.0")
node = NCPNode.from_subprocess(graph=graph, brick_dir=brick_dir, binary="ncp-mcp-server")
```

Full producer + consumer walkthrough: [`examples/workflows/README.md`](examples/workflows/README.md).

> **Security note.** Archive SHA-256 verification proves integrity against the publisher's supplied digest. It does not authenticate the publisher and is not a claim that third-party code is safe to execute.

## Documentation

- [Python SDK reference](docs/PYTHON_SDK.md)
- [MCP adapter reference](docs/MCP_ADAPTER.md)
- [LangGraph adapter reference](docs/LANGGRAPH_ADAPTER.md)
- [Workflow packaging and consumption](examples/workflows/README.md)
- [Protocol specification](spec/ncp-v0.2.3.md)
- [Compatibility and versioning policy](docs/COMPATIBILITY.md)
- [Install guide](docs/INSTALL.md)
- [Benchmarks and methodology](BENCHMARK.md)
- [Roadmap](docs/ROADMAP.md)
- [Contributing](CONTRIBUTING.md)

## Benchmarks

Runtime microbenchmarks live under [`bench/`](bench/). They exercise the runtime's execution path, not AI-agent workloads. The cost case for agents is made by replacing routine model calls with Bricks in the applications you build; see the [lead-qualification vs LLM example](examples/python-sdk/lead-qualification-vs-llm/).

## Repository layout

| Path | Contents |
|---|---|
| `runtime/` | Rust execution runtime |
| `crates/ncp-mcp-server/` | MCP server crate |
| `bricks/` | Reference brick crates |
| `python/ncp-runtime/` | Python SDK (bundles the runtime binary) |
| `python/ncp-langgraph/` | LangGraph adapter |
| `examples/` | Graphs, bricks, workflows, and integration demos |
| `spec/` | Protocol specification |
| `docs/` | Design docs, roadmap, adapter guides, compatibility matrix |

## Get involved

Issues and discussions live on GitHub. The contribution guide is in [CONTRIBUTING.md](CONTRIBUTING.md). All commits require a [DCO sign-off](https://developercertificate.org/) (`git commit -s`).

## License

Apache 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
