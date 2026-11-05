<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# ncp-runtime

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![PyPI](https://img.shields.io/pypi/v/ncp-runtime.svg)](https://pypi.org/project/ncp-runtime/)

Python SDK for the [Neural Computation Protocol (NCP)](https://github.com/madeinplutofabio/neural-computation-protocol) runtime. Run NCP graphs from any Python application: spawn and talk to the `ncp` runtime binary, get structured per-terminal results, iterate the execution trace.

NCP lets you run routine agent work as small, sandboxed WebAssembly Bricks wired into graphs, so you can skip unnecessary language-model calls and reuse the same workflow across Python, MCP, and LangGraph. See the [project README](https://github.com/madeinplutofabio/neural-computation-protocol#readme) for benchmarks, architecture, and when NCP is worth it.

## Install

On **supported platforms** (Linux x86-64 glibc 2.28+, macOS 11+ ARM64 / 10.13+ x86-64, Windows x86-64) the wheel bundles a verified copy of the `ncp` runtime binary:

```bash
pip install ncp-runtime
```

For **workflow consumption and distribution** (install, run, and share versioned workflow archives), install the optional extra:

```bash
pip install 'ncp-runtime[workflows]'
```

Everywhere else, `pip install ncp-runtime` installs the Python-only fallback wheel without a bundled binary. Install the runtime separately and either keep `ncp` on `PATH` or pass an absolute path to `NCP(binary=...)`:

```bash
cargo install ncp-runtime --locked
```

The **MCP server** (`ncp-mcp-server`, [crates.io](https://crates.io/crates/ncp-mcp-server)) and the **LangGraph adapter** (`ncp-langgraph`, [PyPI](https://pypi.org/project/ncp-langgraph/)) are **not** installed by `pip install ncp-runtime`. Install them separately when you need them.

## Verify the install

```bash
python -m ncp_runtime.demo
```

Resolves the `ncp` binary, runs a tiny bundled graph, prints `result_type` plus a one-line summary per terminal. Exit 0 on `Success`.

## Quick start: run your own graph

Replace the paths with your own graph and brick directory:

```python
from ncp_runtime import NCP

ncp = NCP()

result = ncp.run(
    graph="/abs/path/to/your-graph.yaml",
    brick_dir="/abs/path/to/your-bricks",
    inputs={"company_url": "https://example.com"},
)

if result.result_type == "Success":
    print(result.terminals[0].output)
else:
    print(result.result_type, result.terminals[0].error)

for record in result.trace():
    print(record)
```

Prefer deterministic cleanup? Use the context-manager form:

```python
with ncp.run(graph="...", brick_dir="...", inputs={...}) as result:
    ...
```

Graph-level `Failure` is a normal return, not an exception. Subprocess-side problems raise an `NCPSDKError` subclass. See [`docs/PYTHON_SDK.md`](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/PYTHON_SDK.md) for the full contract.

## Workflows: install once, run by name

Once a workflow has been packaged and installed, run it by name (no paths, no manifests to wrangle):

```python
from ncp_runtime import NCP

# Prerequisite: the workflow is already installed under $NCP_WORKFLOW_HOME,
# e.g. with `python -m ncp_runtime.workflows install ./archive.tar.gz --sha256 ...`.
result = NCP().run_workflow(
    "com.my-vendor.my-workflow",
    inputs={"text": "hello"},
    version="0.1.0",
)
```

End-to-end producer + consumer walkthrough (package your own workflow, publish its SHA-256, install it in a fresh environment, and run it from Python, MCP, or LangGraph): [`examples/workflows/README.md`](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/examples/workflows/README.md).

## Compatibility

v0.4.0 of this SDK pins `SUPPORTED_RUNTIME_VERSIONS = ("0.4.0",)`. The published wheel bundles the matching `ncp` 0.4.0 runtime binary on supported platforms. See [`docs/COMPATIBILITY.md`](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/COMPATIBILITY.md) for the coordinated-release matrix.

## Scope and limitations (honest)

- **Sync only.** `NCP().run(...)` is a synchronous call. Native async support is a future version.
- **One subprocess per call.** Each `run()` starts a new `ncp` process. Persistent execution and process pooling are not in 0.4.0.
- **No brick authoring.** This SDK runs graphs; authoring remains the domain of the Rust brick crates.
- **No MCP in the critical path.** Direct `ncp run --all-terminals` invocation; no MCP stdio dialog.

See [`docs/PYTHON_SDK.md`](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/PYTHON_SDK.md) §13 for the full non-goals list.

## Not to be confused with

Aviz Networks publishes an unrelated package named `ncp-sdk` that also uses an `ncp` import namespace. This package publishes under the distribution name `ncp-runtime` and imports as `ncp_runtime` to avoid any collision.

## License

Apache-2.0. See [LICENSE](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/LICENSE) and [NOTICE](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/NOTICE).

## Links

- [NCP project](https://github.com/madeinplutofabio/neural-computation-protocol)
- [Design doc (`docs/PYTHON_SDK.md`)](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/PYTHON_SDK.md)
- [Workflow packaging and consumption](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/examples/workflows/README.md)
- [LangGraph adapter (`ncp-langgraph`)](https://pypi.org/project/ncp-langgraph/)
- [MCP adapter (`ncp-mcp-server`)](https://crates.io/crates/ncp-mcp-server)
- [Compatibility matrix](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/COMPATIBILITY.md)
- [Changelog](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/python/ncp-runtime/CHANGELOG.md)
- [Issues](https://github.com/madeinplutofabio/neural-computation-protocol/issues)
