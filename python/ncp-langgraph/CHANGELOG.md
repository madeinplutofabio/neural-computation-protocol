<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# Changelog

All notable changes to `ncp-langgraph` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Starting with 0.4.0, this adapter advances together with the other
maintained NCP implementation packages as a coordinated release
cohort. See [`docs/COMPATIBILITY.md`](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/COMPATIBILITY.md).

## [0.4.0] - 2026-11-05

First coordinated NCP release.

### Changed

- Package version `0.1.0` -> `0.4.0` to align with the coordinated
  NCP 0.4.0 release cohort. No adapter API or behavior change
  versus `0.1.0`; the revalidation that the adapter still
  interoperates with the 0.4.0 runtime and the 0.4.0
  `ncp-mcp-server` is the deliberate signal carried by the bump.
- `clientInfo.version` sent in the MCP `initialize` frame follows
  `__version__` and now reports `0.4.0`.

### Unchanged

- `NCPNode`, `call_ncp_graph`, `RunnerResult`, the exception
  hierarchy, state input/output semantics, and the subprocess
  runner: all unchanged versus 0.1.0.
- Still requires `ncp-mcp-server` on `PATH` (now v0.4.x) and
  `langgraph >= 1.0.0, < 2.0.0`.

## [0.1.0] - 2026-05-29

First public release. Publishes the LangGraph adapter for the Neural
Computation Protocol to PyPI.

### Added

- **`NCPNode`** -- LangGraph-callable wrapper around an `ncp-mcp-server`
  graph. Locked classmethod factory `NCPNode.from_subprocess(...)`
  returns a callable instance that registers as a LangGraph node via
  `builder.add_node(name, instance)`.
- **`call_ncp_graph(...)`** -- lower-level function for callers that
  want NCP from Python without LangGraph state semantics. Returns a
  `RunnerResult` directly.
- **`RunnerResult`** -- frozen dataclass carrying the graph's terminal
  output, the full MCP `structuredContent`, and trace metadata
  (`result_type`, `trace_id`, `trace_path`).
- **Exception hierarchy** -- `NCPError` (base), `NCPSubprocessError`,
  `NCPInvocationError` (carries `.structured_content`, `.result_type`,
  `.trace_id`, `.trace_path`), `NCPTimeoutError`,
  `NCPAmbiguousToolError`.
- **State input semantics (locked design doc §4.2):** whole-state
  pass-through by default; `input_key=...` narrows to
  `state[input_key]`.
- **State output semantics (locked design doc §4.1):** partial
  state-update dict with exactly two keys (configurable `output_key`,
  `trace_key`). Equal keys are rejected at construction with
  `ValueError`.
- **State immutability (locked design doc §4.4):** enforced LOCALLY in
  `NCPNode.__call__` via `copy.deepcopy(arguments)`. Does NOT rely
  transitively on the runner's non-mutation behavior.
- **Subprocess runner:** spawns `ncp-mcp-server` per call, drives the
  locked 4-frame MCP dialog (`initialize`,
  `notifications/initialized`, `tools/list`, `tools/call`), closes
  stdin, and asserts a clean post-dialog exit (regression sentinel
  for the rmcp tokio time-driver panic discovered in Phase 3A.2-E).
- **Strict JSON discipline:** `json.dumps(..., allow_nan=False)`
  everywhere; rejects `NaN` / `Infinity` / `-Infinity` at the Python
  boundary.
- **Cross-platform timeout** via `threading.Timer` (not
  `signal.SIGALRM`); works on Linux, macOS, Windows.
- **UTF-8 stdio pinning** via `subprocess.Popen(encoding="utf-8")`;
  MCP JSON is UTF-8 by spec.
- **stderr captured to a tempfile** (NOT `subprocess.PIPE`) to avoid
  the pipe-buffer deadlock hazard. Tempfile is kept on
  `NCPSubprocessError` / `NCPTimeoutError` (diagnostic tail included
  in exception message); deleted on success, `NCPInvocationError`,
  and `NCPAmbiguousToolError`.
- **Library-boundary type checks:** `arguments` must be a `dict` at
  runtime; `tools/list` entries validated upfront; cross-check
  between MCP `isError` and `result_type` per locked
  `docs/MCP_ADAPTER.md` §6 truth table.
- **PEP 561 `py.typed` marker;** `mypy --strict` clean over `src/`.
- **Documentation:** binding design contract in
  [`docs/LANGGRAPH_ADAPTER.md`](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/LANGGRAPH_ADAPTER.md);
  quick-start in this package's README; adopter example in
  [`examples/langgraph/`](https://github.com/madeinplutofabio/neural-computation-protocol/tree/main/examples/langgraph)
  (stubbed on `echo-pipeline` until issue
  [#29](https://github.com/madeinplutofabio/neural-computation-protocol/issues/29)
  ships the real lead-qualification graph).

### Requirements

- Python 3.10+
- `ncp-mcp-server v0.1.x` on `PATH` (separate Rust binary; install via
  `cargo install ncp-mcp-server --version 0.1.0 --locked`). Pass an
  absolute path via `NCPNode.from_subprocess(binary=...)` to bypass
  `PATH`.
- LangGraph 1.x (`langgraph>=1.0.0,<2.0.0`).

### v0.1.0 limitations (intentional non-goals)

- **Sync only.** `NCPNode.__call__` is synchronous. Native async
  support (`NCPAsyncNode`) is a v0.2.0+ addition.
- **One subprocess per call.** Each invocation spawns a fresh
  `ncp-mcp-server` process. Persistent pool is a v0.2.0+ perf
  optimization.
- **One graph per `NCPNode` instance.** Multi-graph adapter instances
  are a v0.2.0+ addition.
- **Subprocess invocation only.** PyO3 direct binding and Streamable
  HTTP transport are deferred (see
  [`docs/LANGGRAPH_ADAPTER.md`](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/LANGGRAPH_ADAPTER.md) §11
  and future-work issues
  [#34](https://github.com/madeinplutofabio/neural-computation-protocol/issues/34)
  and
  [#36](https://github.com/madeinplutofabio/neural-computation-protocol/issues/36)).

[0.1.0]: https://pypi.org/project/ncp-langgraph/0.1.0/
