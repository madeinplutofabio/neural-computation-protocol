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

**Not everything an AI agent does needs an AI model.**

Many agents spend time and tokens asking language models to perform routine tasks that ordinary software could handle faster, more cheaply, and with more predictable results.

NCP lets developers move that work into **reusable, sandboxed WebAssembly workflows**, keeping AI models for the tasks that actually need them.

And unlike scattered scripts or one-off integrations, NCP workflows can be **packaged once, distributed, installed, and reused across different AI agents and supported frameworks**.

**Use AI when you need intelligence. Use NCP when you need fast, predictable, repeatable computation.**

## What the benchmarks show

When an agent can avoid language-model calls for work that doesn't require them, the difference can be substantial.

NCP's reference benchmark uses a **simulated 200 ms LLM call** and measures mean graph-execution latency across mixed workloads:

| Requests handled without an LLM | Average execution time | Improvement |
|---|---|---|
| 0% (LLM-only baseline) | 200.6 ms | Baseline |
| 50% | 100.3 ms | ~2x faster |
| 90% | 20.1 ms | **~10x faster** |
| 97% | 6.1 ms | **~33x faster** |

The runtime itself adds little overhead. A single-step graph using a trivial WASM Brick measured **15 microseconds median execution time on Linux/WSL2**, after loading and compilation.

**What about costs?** If model calls dominate your application's spending, avoiding 90% of them can reduce that portion of the bill by roughly 90%, before accounting for deterministic computation and infrastructure.

These are reproducible synthetic benchmarks, not guaranteed production results. Actual savings depend on how much work can be handled reliably without an LLM, the complexity of the work, and the models being replaced.

[Full benchmark results and methodology](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/BENCHMARK.md) | [Cost model](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/COST_MODEL.md) | [Lead-qualification comparison](https://github.com/madeinplutofabio/neural-computation-protocol/tree/main/examples/python-sdk/lead-qualification-vs-llm)

## Build once. Package once. Reuse across AI agents.

**No repository cloning. No Rust toolchain for workflow consumers on supported platforms. No rebuilding WASM components. No manual manifest assembly.**

NCP packages a graph and its referenced WebAssembly Bricks, manifests, and schemas into a **self-contained, versioned workflow archive**.

Once built, that workflow can be shared and installed by another team, application, or AI agent without recreating the original development environment.

The process is straightforward:

1. **Build:** Compose reusable WASM Bricks into a graph.
2. **Package:** Bundle the graph and its dependencies into a versioned archive.
3. **Share:** Distribute the archive with its SHA-256 digest, using GitHub Releases, your own file hosting, or another distribution channel.
4. **Install:** Verify and install the archive without cloning the source repository or recompiling its Bricks.
5. **Run:** Invoke the installed workflow by name from Python, or integrate it through MCP or LangGraph.

**One workflow. Multiple agents. No duplicated business logic.**

The Python SDK bundles the NCP runtime binary on supported platforms. Workflow consumers don't need the author's source code, build tools, or development dependencies. Different workflow versions can coexist, so updates don't automatically replace an existing installation.

The MCP server and LangGraph adapter are separate integrations, installed only when needed. NCP doesn't require a central workflow registry, an account, or a hosted NCP service.

[See the complete workflow packaging and installation walkthrough](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/examples/workflows/README.md)

## Why NCP?

- **Lower AI costs.** Avoid unnecessary language-model calls for routine work. Fewer calls mean fewer tokens billed.
- **Faster responses.** Handle deterministic tasks without waiting for model inference.
- **Easy distribution and reuse.** Package workflows with their referenced components, install them by name and version, and reuse them across supported agent integrations.
- **Sandboxed execution.** Bricks execute as WebAssembly modules through Wasmtime. The reference runtime exposes no filesystem, network, or WASI imports to Bricks by default, and enforces memory and output-size limits.
- **Predictable and traceable.** Deterministic Bricks produce repeatable results under the same inputs and execution conditions. Execution traces help developers understand what ran and how results were routed.
- **Local-first and open source.** Run NCP on infrastructure you control, without depending on a proprietary NCP cloud service or central registry.

NCP doesn't replace language models, agent frameworks, or general-purpose programming. It gives them a reusable execution layer for work that doesn't need to be performed by an LLM.

## A practical example

Imagine your company uses four AI agents:

- A website assistant that qualifies incoming leads.
- A CRM agent that evaluates prospects.
- A customer-support agent that routes enquiries.
- A partner-facing agent that processes referrals.

All four need to apply the same lead-qualification rules.

You could implement those rules separately in every application, creating multiple codebases to maintain. Or you could ask a language model to evaluate every lead, even when the criteria are already known.

**With NCP, developers implement the qualification logic once.**

They compose the deterministic Bricks into a graph, package it as a versioned workflow, and distribute it to the applications that need it.

Each application installs the same workflow version and invokes it through its supported integration.

The result:

- Fewer unnecessary model calls.
- One implementation of the qualification rules.
- Consistent, repeatable results.
- Easier distribution and deliberate version updates.
- Sandboxed execution with traceable outcomes.

A working [two-Brick lead-qualification example](https://github.com/madeinplutofabio/neural-computation-protocol/tree/main/examples/graphs/lead-qualification) demonstrates the approach with 11 reproducible input fixtures.

## Why not just use Python?

**You absolutely can, and sometimes you should.**

If one application needs a few simple rules, ordinary Python functions may be all you need. NCP isn't intended to make a trivial function magically faster.

The challenge changes when that same computation is needed by several AI agents, across different frameworks, applications, or development teams.

With standalone application code, developers may need to copy implementations, manage dependencies separately, maintain multiple integrations, and coordinate updates across projects.

NCP addresses that problem by providing:

- **Portable artifacts:** Package the graph and its referenced components into a distributable workflow archive.
- **Simpler consumption:** Install an existing workflow without rebuilding its WASM components or manually assembling its graph and manifests.
- **Consistent execution:** Run those components through the same WebAssembly-based execution model.
- **Framework integration:** Reuse installed workflows through Python, MCP, and LangGraph.
- **Explicit version management:** Install multiple versions side by side and select the one an application needs.
- **Execution visibility:** Inspect structured results and traces instead of treating the workflow as an opaque operation.

**Python can solve an individual task. NCP helps turn that solution into a reusable, versioned capability across AI systems.**

## Quick start

Install the Python SDK with workflow support:

```bash
pip install 'ncp-runtime[workflows]'
```

On supported platforms, the wheel includes the NCP runtime binary. No separate Rust build is required.

Run the included demo:

```bash
python -m ncp_runtime.demo
```

The demo executes a bundled graph and prints its result and terminal outputs, including a successful echo computation.

No repository clone or separately installed workflow is needed for this first run.

### Package and install a reusable workflow

Once you've built your own graph and Bricks, the workflow packager creates an archive containing the graph and its referenced artifacts.

```bash
python -m ncp_runtime.workflows package \
  --graph ./my-graph.yaml \
  --brick-dir ./bricks \
  --name com.my-vendor.my-workflow \
  --version 0.1.0 \
  --runtime-compat ">=0.4.0,<0.5.0" \
  --out my-workflow-0.1.0.tar.gz
```

Share the archive and its expected SHA-256 digest. In another environment, install it with:

```bash
python -m ncp_runtime.workflows install \
  ./my-workflow-0.1.0.tar.gz \
  --sha256 EXPECTED_ARCHIVE_SHA256
```

These commands illustrate packaging an existing graph; the `my-graph.yaml`, `bricks/`, and archive files are user-provided, not generated by the initial demo.

### Run the installed workflow from Python

```python
from ncp_runtime import NCP

result = NCP().run_workflow(
    "com.my-vendor.my-workflow",
    inputs={"message": "hello"},
    version="0.1.0",
)

if result.result_type == "Success":
    print(result.terminals[0].output)
```

The input fields must match the installed workflow's expected input contract.

### Use the same workflow from MCP or LangGraph

For MCP-compatible hosts, NCP generates the configuration for an installed workflow:

```bash
python -m ncp_runtime.workflows mcp-config \
  com.my-vendor.my-workflow \
  --version 0.1.0 \
  --format snippet
```

For LangGraph, resolve the installed workflow and use its paths with `NCPNode.from_subprocess()`.

The MCP server and LangGraph adapter require separate installation, but neither needs a separate implementation of the workflow's business logic.

**[Complete producer and consumer walkthrough](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/examples/workflows/README.md)**

## Security and execution boundaries

NCP executes Bricks in a constrained WebAssembly environment.

The reference runtime uses an empty Wasmtime import surface for Bricks by default, with no WASI, filesystem, or network functions exposed. Memory and output-size limits are enforced. CPU fuel metering is not enabled by default.

Workflow packaging and installation validate referenced artifacts and their committed digests. Installation also checks the supplied archive SHA-256 before extraction.

**Important:** SHA-256 verification establishes integrity against an expected digest, not the identity or trustworthiness of the publisher. WebAssembly sandboxing reduces the capabilities exposed to a Brick; it is not a guarantee that arbitrary third-party code is safe. Security also depends on the runtime, surrounding integrations, and deployment environment.

See [SECURITY.md](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/SECURITY.md) for the security model and reporting instructions.

## Documentation

| Guide | What you'll find |
|---|---|
| [Workflow packaging and consumption](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/examples/workflows/README.md) | Package, distribute, install, manage, and run workflows |
| [Python SDK](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/PYTHON_SDK.md) | Python execution API, results, traces, and errors |
| [MCP adapter](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/MCP_ADAPTER.md) | Expose NCP graphs to MCP-compatible hosts |
| [LangGraph adapter](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/LANGGRAPH_ADAPTER.md) | Use NCP as LangGraph nodes |
| [Installation](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/INSTALL.md) | Runtime and platform installation options |
| [Benchmarks](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/BENCHMARK.md) | Measurement methodology, latency and throughput results |
| [Cost model](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/COST_MODEL.md) | Estimate token and inference-cost savings |
| [Protocol specification](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/spec/ncp-v0.2.3.md) | Normative NCP execution and graph contract |
| [Compatibility and versioning](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/COMPATIBILITY.md) | NCP release cohort, supported versions, and migration rules |
| [Roadmap](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/ROADMAP.md) | Development milestones and future work |
| [Adoption guide](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/ADOPTION_GUIDE.md) | Choosing and integrating suitable workloads |

## Repository layout

| Path | Contents |
|---|---|
| `runtime/` | Rust execution runtime using Wasmtime |
| `crates/ncp-mcp-server/` | MCP server |
| `bricks/` | Reference WASM Brick crates |
| `python/ncp-runtime/` | Python SDK and workflow packaging/consumption |
| `python/ncp-langgraph/` | LangGraph adapter |
| `examples/` | Graphs, Bricks, workflows, and integration examples |
| `spec/` | NCP protocol specification |
| `schemas/` | Graph and Brick manifest schemas |
| `tools/` | Manifest validation tooling |
| `docs/` | Architecture, development, and integration documentation |
| `bench/` | Benchmark datasets and results |

## Get involved

NCP is open source under the Apache 2.0 license.

Issues, contributions, and discussions are welcome on [GitHub](https://github.com/madeinplutofabio/neural-computation-protocol).

Start with [CONTRIBUTING.md](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/CONTRIBUTING.md). All commits require a [Developer Certificate of Origin](https://developercertificate.org/) sign-off using `git commit -s`.

Security-sensitive reports should follow [SECURITY.md](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/SECURITY.md).

## License

Apache 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
