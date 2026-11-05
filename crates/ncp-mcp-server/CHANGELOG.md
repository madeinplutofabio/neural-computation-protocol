<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# Changelog — `ncp-mcp-server`

All notable changes to the `ncp-mcp-server` crate are documented in this
file. The root [`CHANGELOG.md`](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/CHANGELOG.md)
covers `ncp-runtime`, the NCP specification, and other project-wide
changes.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-11-05

First coordinated NCP release (see
[`docs/COMPATIBILITY.md`](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/COMPATIBILITY.md)).
Starting with 0.4.0, this crate advances together with the other
maintained NCP implementation packages.

### Changed

- Package version `0.1.0` -> `0.4.0` to align with the coordinated
  NCP 0.4.0 release cohort. No code change versus `0.1.0`; the
  revalidation that the MCP server still interoperates with the
  0.4.0 runtime is the deliberate signal carried by the version
  bump.
- `ncp-runtime` dependency bumped to `0.4.0` to track the
  coordinated runtime version. Local `path` dependency unchanged;
  the `version` requirement is what crates.io consumers see.

### Unchanged

- MCP protocol target: 2025-11-25 over stdio only (Streamable HTTP
  still out of scope).
- CLI surface, tool-list behavior, trace emission, startup
  validation, error reporting: all unchanged versus 0.1.0.

## [0.1.0] - 2026-05-28

### Added
- Stdio MCP adapter binary.
- One NCP graph exposed as one MCP tool.
- Multi-graph support via repeated `--graph`.
- Runtime-loaded dynamic tool list.
- Object-shaped MCP arguments passed verbatim as graph root input.
- `structuredContent` + text mirror response shape.
- Per-call `trace_id` and optional `trace_path`.
- `--trace-dir` support with per-call JSONL traces.
- `--check` startup validation mode.
- Process-level MCP protocol tests.
- Graceful shutdown regression test for rmcp/tokio time driver.
- CI smoke script and `mcp-smoke` required check.

### Notes
- Targets MCP spec 2025-11-25 over stdio only.
- Streamable HTTP is out of scope for v0.1.0.
- Rust module API is internal; CLI is the stable surface.
