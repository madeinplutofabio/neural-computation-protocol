<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# Changelog

All notable changes to `ncp-langgraph` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

`ncp-langgraph` is versioned independently from `ncp-runtime` and
`ncp-mcp-server`; bumps reflect adapter API or behavior changes only.

## [Unreleased]

### Added

- Package skeleton (`pyproject.toml`, src-layout, hatchling backend).
- Public API surface stubs: `NCPNode`, `NCPNode.from_subprocess(...)`,
  `call_ncp_graph(...)`, `RunnerResult` dataclass, and exception
  hierarchy (`NCPError`, `NCPSubprocessError`, `NCPInvocationError`,
  `NCPTimeoutError`, `NCPAmbiguousToolError`). Executable API paths
  (`NCPNode.from_subprocess(...)`, `NCPNode.__call__(...)`, and
  `call_ncp_graph(...)`) raise `NotImplementedError` until PR C and
  PR D land the implementations.
- PEP 561 `py.typed` marker so type-checkers consume the package's
  inline type information.
- Build/lint/test tooling: `ruff` (lint + format), `mypy --strict` over
  `src/`, `pytest`. Available via `pip install -e .[dev]`.
- `langgraph-test` CI job in `.github/workflows/rust.yml` (advisory
  until Phase 3A.3 PR G merges).

### Notes

- This package skeleton uses version `0.1.0.dev0` and is NOT intended
  for end-user installation. It exists to lock the package layout,
  build pipeline, and CI gate before implementation work begins.
- The locked design contract for v0.1.0 lives in
  [`docs/LANGGRAPH_ADAPTER.md`](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/LANGGRAPH_ADAPTER.md).

[Unreleased]: https://github.com/madeinplutofabio/neural-computation-protocol/commits/main/python/ncp-langgraph
