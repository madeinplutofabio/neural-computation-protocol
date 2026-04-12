<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# NCP Roadmap

## Phase 1: Spec + Validator — COMPLETE

Canonical specification (v0.2.3), JSON Schemas (Draft 2020-12), 24 cross-field invariant rules, `ncp-validate` CLI, test suite with positive and negative fixtures, CI.

**Tag:** `v0.2.3`

## Phase 2: Reference Runtime — COMPLETE

Single-process Rust runtime that loads a Graph, executes WASM Bricks, routes edges deterministically, and emits traces.

Delivered:

- Graph and Brick manifest loading with full validation
- WASM sandbox via Wasmtime 43 with memory limits
- Model B ABI (alloc/free/invoke) with CBOR envelope (sorted keys, cross-runtime determinism)
- Result boundary validation (Success / LowConfidence / Failure per Section 9.2)
- Deterministic routing per Section 7.4.1 (on_error priority, on_success weight/threshold, fan-out)
- Field mapping with dot-path resolution and deep merge
- SHA-256 artifact digest verification
- JSON Lines trace emission (Section 11.1)
- FIFO queue orchestration with safety budgets (max_steps, max_queued)
- Reference echo Brick (Rust → WASM) and trap Brick for testing
- End-to-end test graphs: single-node, two-node chain with routing, trap handling

Not in scope (deferred): replay (Section 12.2), runtime intrinsics (Section 6.6.2), carry_state lifecycle, graph refs.

**Tag:** `v0.3.0`

## Phase 3 — Tracks

Phase 3 is split into independent tracks that can progress in parallel.

### Phase 3A: Integrations (Adoption Track)

Goal: NCP can be used from existing agent stacks.

- MCP server adapter: one MCP tool = one NCP graph
- LangGraph node wrapper: an NCP graph as a node in a larger workflow
- Python + TypeScript SDKs to pack bricks and run graphs
- Packaging: prebuilt binaries (GitHub Releases), `cargo install`, Docker image
- Documentation: "Using NCP from X" guides, trace consumption guide

### Phase 3B: Runtime Completeness (Correctness Track)

Goal: more of the spec becomes executable.

Candidates (pick one, make it real, repeat):

- Runtime intrinsics: safe, deterministic `ncp_runtime.*` host imports (Section 6.6.2)
- Graph refs: slot resolution and ref_consistency enforcement
- Carry state lifecycle: init, TTL, version migration
- Fan-in activation policies (Section 7.0)
- Wall-clock timeout enforcement (not just fuel)

### Phase 3C: Production Profile (Hardening Track)

Goal: safe to run in services and semi-hostile environments.

- Tighter Wasmtime config and host import policy
- Stronger artifact trust story (signature chains, allowlists)
- Clearer threat model and security posture
- Resource metering beyond memory (CPU fuel, I/O budgets)

## Phase 4: Distribution + Learning

- Brick registry (or GitHub-based packs)
- Full conformance suite for third-party runtimes
- Optional adaptive routing / learned thresholds
