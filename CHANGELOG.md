<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# Changelog

All notable changes to the NCP specification and its reference tooling
(schemas, examples, validator, runtime) are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Brick versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). NCP protocol versions follow the versioning policy in the spec (v0.2.x patch-compatible, breaking changes require v0.3.0).

## [0.3.4] — 2026-MM-DD

### Added
- **`docs/INSTALL.md`**: install matrix for adopters — GitHub Releases archives,
  Docker (GHCR), `cargo install` from crates.io, and build-from-source — each
  with per-OS commands and checksum verification. Architecture coverage table
  at top makes Apple Silicon vs Intel + Linux x86_64 vs aarch64 self-service
- **`runtime/Cargo.toml`** crates.io publish metadata: `description`,
  `repository`, `homepage`, `readme = "../README.md"`, `keywords` (`wasm`,
  `agentic-ai`, `graph`, `deterministic`, `ncp`), `categories` (`wasm`,
  `command-line-utilities`, `asynchronous`), and explicit `include` array.
  Verified end-to-end via `cargo publish --dry-run --locked` and unpacked-crate
  `cargo install --path . --bin ncp --locked` (produces working `ncp` binary)
- **`runtime/LICENSE`** + **`runtime/NOTICE`**: verbatim copies of the
  repo-root files. Cargo's `include` array silently strips `..` paths, so
  local copies are needed for Apache 2.0 §4(d) compliance in the published
  `.crate` (`readme = "../README.md"` continues to work because cargo
  special-cases the `readme` field)
- **`README.md`**: top-level "Install" section above "Quick start", linking
  to `docs/INSTALL.md`. Pre-built install paths are now the default story;
  building from source is positioned as the developer path

## [0.3.3] — 2026-05-26

### Added
- **Rust CI workflow** (`.github/workflows/rust.yml`): fmt, clippy with `-D warnings`,
  cross-platform `cargo test -p ncp-runtime` matrix on pinned OS images
  (`ubuntu-24.04`, `macos-14`, `windows-2022`); separate `wasm-build` job for
  brick crates targeting `wasm32-unknown-unknown`; per-job `timeout-minutes`;
  explicit `permissions:` scope (`contents: read`, `actions: write` for cache);
  `--locked` on every cargo invocation
- **Smoke integration tests** (`runtime/tests/smoke.rs`): exercise every runnable
  example graph end-to-end via `RuntimeContext::load` + `.execute` (in-process,
  no binary spawn); paths derived from `env!("CARGO_MANIFEST_DIR")` for OS-independent
  resolution; trap-pipeline assertion accepts any non-empty `error_class` to remain
  robust across Wasmtime version drift
- **Citation metadata**: `CITATION.cff` (GitHub "Cite this repository" panel) and
  `.zenodo.json` (Zenodo archive metadata), both bound to concept DOI
  `10.5281/zenodo.19570209`
- **Branch protection**: 6 new Rust CI check contexts required alongside the
  existing DCO + validate + `wasm-digest-check` set; force-push and branch
  deletion blocked on `main`; web-based commit sign-off enforced
- **`docs/BRANCH_PROTECTION.md`**: audit trail for the branch-protection ruleset
  (current required checks, exact `gh api` command sequence used, rollback
  procedure, schema-fallback note)

### Fixed
- `docs/ADOPTION_GUIDE.md`: every `ncp` CLI example now includes the required
  `run` subcommand (previously copy-pasted commands failed with
  `unrecognized argument`). `ncp-bench` invocations unaffected (positional graph
  argument, no subcommand)

### Changed
- `runtime/Cargo.toml`: crate version bumped `0.1.0` → `0.3.3` to align with
  the release tag and eliminate the `cargo install ncp-runtime` version
  surprise. `Cargo.lock` updated to match (only the self-version entry).
- Brick crates (`ncp-echo`, `ncp-classifier-stub`) intentionally remain at
  crate version `0.1.0`. Their Rust crate version is decoupled from the
  NCP-brick-manifest version (declared in `examples/bricks/*/manifest.yaml`,
  also `0.1.0`). The two will align when bricks are published to crates.io
  in Phase 3A.

## [0.3.2] — 2026-04-14

### Added
- Apache-2.0 + `NOTICE` + DCO + No-CLA governance finalized
- Zenodo DOI archive (`10.5281/zenodo.19570209`) via Zenodo's GitHub integration —
  citable prior art for the protocol, reference runtime, and benchmark methodology
- DCO enforced as required status check on `main` via branch ruleset
  "Main Protection"
- `README.md` finalized: DOI / CI / license / release badges, headline benchmark
  summary, and "Numbers you can quote" framing

## [0.3.1] — 2026-04-13

### Added
- **Benchmark harness** (`runtime/src/bin/ncp-bench.rs`): configurable warmup/runs, JSON output,
  LLM latency simulation via `--simulate-llm-ms` with `ExecuteHooks.on_invoke`,
  `--dataset` mode for mixed-workload JSONL files with dual timing (execute-only + end-to-end),
  dataset SHA-256 provenance in JSON output, contextual parse error messages,
  `--concurrency N` multi-threaded throughput mode via `Arc<RuntimeContext>`,
  `--cold-start` load+compile timing measurement
- **RuntimeContext library pattern** (`runtime/src/lib.rs`): load/compile once, execute N times;
  pluggable `TraceSink` trait with `enabled()` for zero-cost bench mode
- **Classifier-stub brick** (`bricks/classifier-stub/`): keyword-based sentiment, `bit_exact`,
  `no_std`, returns Success (positive) or LowConfidence/LOW_CONFIDENCE (negative)
- **Support-routing-stubbed graph** (`examples/graphs/support-routing-stubbed/`): classifier →
  echo escalation path with `on_error(LOW_CONFIDENCE)` routing
- **Benchmark datasets** (`bench/datasets/`): 3 deterministic JSONL files (90/10, 97/3, 50/50
  positive/negative mix) with variant rotation for realistic input diversity
- **Benchmark results** (`bench/results/`): 16 Windows + 16 Linux (WSL2) machine-readable JSON
  files covering pure runtime overhead, simulated LLM latency, LLM-only baseline,
  measured mixed synthetic workloads, cold-start timing, and concurrency throughput
- **Benchmark matrix script** (`bench/run-matrix.sh`): runs all 16 configs with one command
- **BENCHMARK.md**: full methodology, results tables (pure overhead, simulated LLM, mixed
  workloads, cold start, concurrency throughput), Claims section with measured LLM-only
  baseline, measured mixed-workload speedups (not modeled), cross-platform comparison
  (Windows vs Linux/WSL2), and explicit caveats
- **COST_MODEL.md**: parameterized cost framework with explicit $/step derivation
  (~$0.00042 per million steps at $0.05/vCPU-hour)
- **README.md**: "Numbers you can quote" subsection with µs, ms, and $ figures

### Changed
- `runtime/Cargo.toml`: added `[lib]` section, `ncp-bench` binary, `default-run = "ncp"`
- `runtime/src/main.rs`: thin CLI wrapper using `ncp_runtime::*` library imports
- `runtime/src/cli.rs`: added `--verbose` flag for per-step diagnostics
- `runtime/src/trace.rs`: added `TraceSink::enabled()` default method; `NullTrace` returns false
- `Cargo.toml` (workspace): added `bricks/classifier-stub` to members

## [0.3.0] — 2026-04-12

### Added
- **Reference runtime** (`runtime/`): single-process Rust runtime executing NCP graphs end-to-end
  - Graph and Brick manifest loading with validation (version range, digest, size, format, limits)
  - WASM sandbox via Wasmtime 43 with `StoreLimits` memory enforcement
  - Model B ABI: `alloc`/`free`/`invoke` with 4-byte LE length-prefixed result buffers
  - CBOR invocation envelope with sorted keys for cross-runtime determinism
  - Result boundary validation: Success / LowConfidence / Failure per Section 9.2
  - Deterministic routing per Section 7.4.1: on_error (priority desc, edge_id asc, single-target), on_success (threshold gating, weight desc, edge_id asc, fan-out)
  - Field mapping: dot-path resolution, deep merge with stable key order
  - SHA-256 artifact digest verification (case-insensitive hex, strict 64-char)
  - JSON Lines trace emission per Section 11.1 (runtime_info + invoke records)
  - FIFO queue orchestration with entry node detection and safety budgets (`--max-steps`, `--max-queued`)
  - 44 unit tests: envelope, result boundary (16), routing conformance (13), field mapping (13)
- **Trap Brick** (`examples/bricks/trap/`): WASM brick that always traps, for testing Section 16.2.1 error handling
- **Test graphs**:
  - `examples/graphs/echo-pipeline/`: single-node echo (validates basic invocation + trace)
  - `examples/graphs/echo-chain/`: two-node chain with on_success routing and field mapping
  - `examples/graphs/trap-pipeline/`: single-node trap (validates COMPUTATION_ERROR path)
- Cargo workspace configuration: `runtime` and `bricks/echo` as workspace members, `runtime` as default
- Rust toolchain pinned to 1.94.0 via `rust-toolchain.toml`
- Workspace-level `[profile.release]` for WASM-optimized builds

### Changed
- `bricks/echo/Cargo.toml`: moved `[profile.release]` to workspace root (eliminates cargo warning)
- `docs/ROADMAP.md`: Phase 2 marked complete, Phase 3 restructured into parallel tracks (3A Integrations, 3B Runtime Completeness, 3C Production Hardening)
- `README.md`: added runtime quick-start section, updated repository structure
- `CONTRIBUTING.md`: added Rust development workflow
- `.gitignore`: added `target/` for workspace-level Rust build cache

### Fixed
- Corrected RFC 8742 (CBOR Sequences) references to RFC 8949 §4.2 (Deterministic Encoding) in spec and design-decisions

### Changed (spec, non-breaking)
- Section 5.2: Canonicalization pinned to deterministic CBOR per RFC 8949 §4.2; JSON requires JCS (RFC 8785); YAML features rejected for digest computation
- Section 16.3: Fixed intro from "MUST provide imports" to "MUST support result-buffer memory management" (alloc/free are Brick exports, not runtime imports)

### Added (spec, non-breaking)
- Section 6.6.2: Runtime intrinsics and external model access — `ncp_runtime.*` import namespace, feature declaration/discovery, conformance profile statement
- Section 7.4.1: Routing evaluation order — deterministic algorithm for on_error matching, on_success threshold/weight ordering, tie-breaking, fan-out dispatch, default weight 0.0, invalid confidence handling
- Section 16.2.1: WASM trap handling — runtime MUST catch traps and convert to Failure with COMPUTATION_ERROR
- `ncp-validate pack` command: stamps manifest templates with real artifact digest and size
- `conformance/`: Test vectors for canonicalization (RFC 8949 §4.2) and routing evaluation order (Section 7.4.1)
- CI: WASM digest check job

## [0.2.3] — 2026-02-17

### Changed
- Result model clarified as a three-variant discriminated union (Success, LowConfidence, Failure)
- Section 9.2 (Structural Boundary Rule) updated for three-variant semantics
- Section 9.5 (Confidence Signaling) updated for LowConfidence variant
- Carry state semantics: `carry_state_next` applies on Success and LowConfidence; `carry_state_side_effects` applies only on Failure

### Fixed
- Resolved contradiction between Section 9.1 (two-variant Result text) and Section 9.2 (Structural Boundary Rule requiring output on LOW_CONFIDENCE)
- Fixed duplicate "Appendix B" header in spec document

### Added
- JSON Schemas (Draft 2020-12) for Brick Manifest, Graph Manifest, Invocation Envelope, and Result Model
- `ncp-validate` CLI: structural (JSON Schema) + cross-field invariant validation for Brick and Graph manifests
- 24 invariant rules (12 brick, 8 graph, 4 cross) with spec section references
- Example manifests from spec appendices (sentiment-gate, llm-escalation, support-routing)
- Test suite with positive and negative fixtures for all invariants
- CI workflow (GitHub Actions)

## [0.2.2] — TBD

### Added
- Artifact extension point (Section 6.2)
- Stochastic model support with `model_pin` and `reproducibility_level` (Section 6.6.1)
- MCP integration appendix (Appendix C)
- Cost metadata (`estimated_cost_per_invoke_usd`) in resource limits
- Carry state side-effects channel (Section 6.9)

## [0.2.0] — TBD

### Added
- Fan-in activation policies (Section 7.0)
- Trigger provenance in invocation envelope (Section 8.1)
- Carry state lifecycle management (Section 10.5)
- Wire format and transport specification (Section 16)
- Conformance suites (Section 15)

## [0.1.0] — TBD

### Added
- Initial draft: Brick and Graph manifest structure
- Core concepts: Brick, Graph, Runtime, State Taxonomy
- Identifier format and Brick bundle specification
- Basic invocation envelope and result model
