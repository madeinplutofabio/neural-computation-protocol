<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# Changelog

All notable changes to `ncp-runtime` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Starting with 0.4.0, this SDK advances together with the other
maintained NCP implementation packages as a coordinated release
cohort. See [`docs/COMPATIBILITY.md`](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/COMPATIBILITY.md)
for the version matrix.

## [0.4.0] - 2026-11-05

First coordinated NCP release. This SDK release ships the workflow
consumption and distribution subpackage, and aligns its version
number with the Rust `ncp-runtime` crate, `ncp-mcp-server`, and
`ncp-langgraph` under the coordinated-release policy.

### Added

- **`ncp_runtime.workflows`**: new subpackage for packaging,
  installing, listing, resolving, inspecting and removing NCP
  workflow archives. Exposes a Python API and a `python -m
  ncp_runtime.workflows` command-line interface with subcommands
  `package`, `install`, `list`, `show`, `resolve`, `remove`,
  `mcp-config`.
- **`NCP.run_workflow(name, inputs, *, version=None, ...)`**:
  invoke an installed workflow by identity rather than by file
  paths. Resolves to an installed `(name, version)` under
  `$NCP_WORKFLOW_HOME` (default `~/.ncp/workflows/`), then
  delegates to `NCP.run()` unchanged.
- **Workflow archive format `ncp-workflow-0.1`**: self-contained
  `<name>-<version>.tar.gz` carrying `workflow.json`, the graph,
  every referenced brick's manifest, WASM and schemas. Install is
  SHA-256-verified before extraction, safe-extracted (no
  traversal, no symlinks, no duplicates), and atomically published
  into `$NCP_WORKFLOW_HOME`.
- **MCP integration without server changes**: `python -m
  ncp_runtime.workflows mcp-config` prints the exact `--graph` and
  `--brick-dir` arguments (or an `mcpServers` snippet) for an
  installed workflow; `ncp-mcp-server` is unchanged.
- **New error subclasses**: `NCPWorkflowError`,
  `NCPWorkflowNotInstalledError`,
  `NCPWorkflowAmbiguousVersionError`, `NCPWorkflowDigestError`,
  `NCPWorkflowRuntimeIncompatibleError`.
- **Optional extra**: `pip install 'ncp-runtime[workflows]'`
  installs `pyyaml` for workflow metadata parsing.

### Changed

- **`SUPPORTED_RUNTIME_VERSIONS`**: tightened from `("0.3.6",)` to
  `("0.4.0",)`. Bundled wheels now ship the 0.4.0 runtime binary.
- Package version `0.1.0` -> `0.4.0` as part of the coordinated
  NCP release cohort. No breaking API change relative to 0.1.0
  beyond the runtime-version pin.

### Documentation

- README trimmed to point at the top-level NCP README and the new
  `docs/COMPATIBILITY.md`.

## [0.1.0] - 2026-11-02

First public release. Publishes the Python SDK for the Neural
Computation Protocol runtime to PyPI.

### Added

- **`NCP`**: synchronous entry point. One instance per SDK consumer;
  one subprocess per `.run()` call. Discovers the `ncp` binary via
  precedence (`binary=` kwarg, bundled wheel binary, `shutil.which("ncp")`)
  and caches the resolved path and version for the instance lifetime.
- **`.run(graph, brick_dir, inputs, *, brick_map=None, max_steps=None)`**:
  invokes `ncp run --all-terminals`, parses structured per-terminal
  results, returns a `Result`.
- **`Result`**: immutable terminal rollup. `.terminals: tuple[Terminal, ...]`,
  `.result_type: str` (rollup), `.trace_path: Path`. SDK owns the
  trace temp file via context manager + `.cleanup()` + `weakref.finalize`
  safety net (exception-safe ownership per design doc §5.1).
- **`Terminal` + `TerminalError`**: one dataclass per terminal with
  `node_id`, `brick_id`, `step`, `result_type`, optional `output`,
  optional `error`. `output` and `error` parsed by field presence;
  LowConfidence terminals can carry both.
- **`Result.trace() -> Iterator[TraceRecord]`**: lazy iterator over
  the JSONL trace file. Typed variants `RuntimeInfoRecord`,
  `InvokeRecord`, `UnknownTraceRecord` (forward compat within
  accepted schema version `ncp-trace-0.1`).
- **Strict `--all-terminals` JSON validation**: four required fields,
  closed-enum `type`, correct field types. Unknown values rejected
  rather than silently treated as `Success`.
- **Rollup rule** `any Failure, else any LowConfidence, else Success`
  matching MCP adapter semantics.
- **Nonzero-exit discipline**: any non-zero exit raises
  `NCPSubprocessError` (allowlist empty in v0.1.0). Graph-level
  `Failure` is a normal return.
- **Exception hierarchy**: `NCPSDKError` base; subclasses
  `NCPBinaryNotFoundError`, `NCPRuntimeVersionError`, `NCPTimeoutError`,
  `NCPSubprocessError` (with `.category`, `.exit_code`, `.binary_path`,
  `.stderr`), `NCPTraceSchemaError`.
- **Subprocess hardening**: argv arrays only; cross-platform
  process-group management (POSIX `start_new_session` + `os.killpg`;
  Windows `CREATE_NEW_PROCESS_GROUP` + `taskkill /T /F`); concurrent
  cap-enforcing reader threads (stdout 16 MiB, stderr 1 MiB) that
  drain to a bit-bucket after cap and signal the main thread to
  terminate the process tree on overflow.
- **Controlled stderr exposure**: stderr contents off exception
  messages by default; full stderr on `NCPSubprocessError.stderr`;
  `NCP_RUNTIME_STDERR_IN_MESSAGE=1` opt-in env var for debugging.
- **Trace file ownership (§5.1)**: exception-safe transfer. SDK
  unlinks the trace tempfile on every failure path, including
  `KeyboardInterrupt` and unexpected exceptions, until ownership
  transfers to a successfully-returned `Result`.
- **Runtime version check**: `--version` queried once per NCP
  instance; parsed version matched against
  `SUPPORTED_RUNTIME_VERSIONS = ("0.3.6",)`. Mismatch raises
  `NCPRuntimeVersionError`.
- **`python -m ncp_runtime.demo`**: bundled onboarding smoke that
  resolves the binary, runs a tiny bundled graph, and prints
  `result_type` plus a one-line summary per terminal. Exit 0 on
  `Success`.
- **PEP 561 `py.typed` marker.** `mypy --strict` clean over `src/`.
- **Documentation**: design contract in
  [`docs/PYTHON_SDK.md`](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/PYTHON_SDK.md);
  quick-start in this package's README.

### Supported runtime versions

- Rust `ncp-runtime` crate 0.3.6, exact pin.

### Requirements

- Python 3.10+
- Compatible `ncp` runtime binary. Bundled by the wheel on supported
  platforms (Linux x86-64 glibc 2.28+, macOS 11+ ARM64 / 10.13+ x86-64,
  Windows x86-64); `cargo install ncp-runtime --locked` or
  `NCP(binary=...)` on the Python-only fallback.
- No runtime Python dependencies.

### v0.1.0 limitations (intentional non-goals)

- **Sync only.** `NCP().run(...)` is synchronous. Native async
  support is a future version.
- **One subprocess per `.run()`.** Each `run()` starts a new `ncp`
  process. Persistent execution and process pooling are outside
  v0.1.0.
- **No brick authoring.** This SDK runs graphs; authoring remains
  the domain of the Rust brick crates.
- **No MCP in the critical path.** Direct `ncp run --all-terminals`
  invocation.
- **No new runtime handshake.** Strict `--all-terminals` JSON
  validation is defensive parsing, not a protocol change.
- See [`docs/PYTHON_SDK.md`](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/PYTHON_SDK.md) §13 for the full non-goals list.

[0.1.0]: https://pypi.org/project/ncp-runtime/0.1.0/
