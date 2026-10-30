<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# ncp-runtime

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![PyPI](https://img.shields.io/pypi/v/ncp-runtime.svg)](https://pypi.org/project/ncp-runtime/)

Python SDK for the [Neural Computation Protocol (NCP)](https://github.com/madeinplutofabio/neural-computation-protocol) runtime.

**This Python distribution is the SDK, not the Rust executable.** It is a thin Python layer that spawns and talks to the `ncp` runtime binary (produced by the Rust `ncp-runtime` crate), returns structured per-terminal results, and exposes an iterator over the execution trace.

Use it to run NCP graphs from any Python application. For LangGraph adapters see [`ncp-langgraph`](https://pypi.org/project/ncp-langgraph/); for MCP hosts see the Rust crate [`ncp-mcp-server`](https://crates.io/crates/ncp-mcp-server). All three sit on top of the same runtime.

## Quick start

Replace the paths below with your own graph and brick directory:

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

## Install

### On supported platforms (Linux x86-64 glibc 2.28+, macOS 11+ ARM64 / 10.13+ x86-64, Windows x86-64)

```bash
python -m pip install ncp-runtime
```

The installed wheel bundles a verified copy of the `ncp` runtime binary. No Rust toolchain required.

### Everywhere else (Python-only fallback)

`pip install ncp-runtime` still succeeds, but the wheel ships without a bundled binary. Install the runtime separately and either keep `ncp` on `PATH` or pass an absolute path to `NCP(binary=...)`:

```bash
cargo install ncp-runtime --locked
```

Pin to a specific version for reproducibility:

```bash
python -m pip install ncp-runtime==0.1.0
```

After install, verify end-to-end with the bundled smoke:

```bash
python -m ncp_runtime.demo
```

Resolves the `ncp` binary, runs a tiny bundled graph, and prints `result_type` plus a one-line summary per terminal. Exit 0 on `Success`.

## Requirements

- Python 3.10+
- Compatible `ncp` runtime binary. v0.1.0 of this SDK pins `SUPPORTED_RUNTIME_VERSIONS = ("0.3.6",)`.
- No runtime Python dependencies.

## v0.1.0 scope and limitations (honest)

- **Sync only.** `NCP().run(...)` is a synchronous call. Native async support is a future version.
- **One subprocess per call.** Each `run()` starts a new `ncp` process. Persistent execution and process pooling are outside v0.1.0.
- **No brick authoring.** This SDK runs graphs; authoring remains the domain of the Rust brick crates.
- **No MCP in the critical path.** Direct `ncp run --all-terminals` invocation; no MCP stdio dialog.

See [`docs/PYTHON_SDK.md`](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/PYTHON_SDK.md) §13 for the full non-goals list.

## Not to be confused with

Aviz Networks publishes an unrelated package named `ncp-sdk` that also uses an `ncp` import namespace. This package publishes under the distribution name `ncp-runtime` and imports as `ncp_runtime` to avoid any collision.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

## Links

- [NCP project](https://github.com/madeinplutofabio/neural-computation-protocol)
- [Design doc (`docs/PYTHON_SDK.md`)](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/PYTHON_SDK.md)
- [LangGraph adapter (`ncp-langgraph`)](https://pypi.org/project/ncp-langgraph/)
- [MCP adapter (`ncp-mcp-server`)](https://crates.io/crates/ncp-mcp-server)
- [Changelog](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/python/ncp-runtime/CHANGELOG.md)
- [Issues](https://github.com/madeinplutofabio/neural-computation-protocol/issues)
