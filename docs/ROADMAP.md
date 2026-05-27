<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# NCP Roadmap

> _Supersedes the Apr 2026 phased roadmap; this "tracks" structure replaces sequential waves._

This roadmap is written to optimize for:
- **Excellence** (correctness, safety, reproducibility, clear contracts)
- **Wide adoption** (2-minute install, composable integrations, great docs)
- **Future-proof foundations** (avoid rewrites; lock stable seams early)

It is split into **tracks** that can progress in parallel:
- **Adoption Track** (integrations + packaging)
- **Correctness Track** (spec → executable behavior)
- **Hardening Track** (production profile: trust, limits, observability)

> Guiding principle: **deterministic first, escalate only when needed** — and make that claim measurable, reproducible, and auditable.

---

## 0) Definitions

### "Production-ready" means

The repo can be used in a real service with confidence, and every merge preserves that:

**Quality gates**
- `cargo fmt --check`
- `cargo clippy --workspace -- -D warnings`
- `cargo test --workspace`
- Smoke-run **every example graph** in CI
- WASM digest verification (no drift)

**Cross-platform**
- CI matrix: Linux + macOS + Windows for `cargo` jobs; digest verification remains Linux-only

**Security posture**
- Threat model document
- Documented sandbox capability profile
- Resource enforcement: **memory + CPU + wall-clock + I/O budgets**
- Public security policy + private disclosure channel

**Supply chain**
- Signed releases (cosign/minisign)
- Reproducible builds verified in CI (byte-identical artifacts)

**Observability**
- Tracing documented + consumable in real stacks (OTel/Jaeger-friendly)

---

### "Widely adoptable" means

A new user can get value quickly without reading the whole spec:

**Installable in < 2 minutes via**
- Prebuilt binaries (GitHub Releases)
- `cargo install`
- Docker image (GHCR)

**Plug-in integration**
- One-line integration with at least **two host stacks** (MCP + LangGraph)

**Ecosystem**
- 10+ reusable brick packs (validators, extractors, routers, policy gates, redaction, retrieval stubs)
- "Write a brick in 10 minutes" starter + cookbook
- At least 3 reference graphs beyond echo/trap/stub

**Evidence**
- Public conformance suite for third-party runtimes
- A real end-to-end case study using an actual LLM (not just simulated latency)

---

## 1) Current baseline

This section reflects the latest released state. Keep it honest and specific; update on every release.

### Completed milestones

#### v0.2.3 — Spec + Validator (2026-02-17)
- Canonical spec (Markdown), JSON Schemas (Draft 2020-12)
- Three-variant Result union (Success / LowConfidence / Failure)
- 24 cross-field invariant rules (12 brick · 8 graph · 4 cross)
- `ncp-validate` CLI (structural + semantic), full test suite, CI

#### v0.3.0 — Reference Runtime (2026-04-12)
- Rust runtime on Wasmtime 43, Model B ABI (`alloc`/`free`/`invoke`)
- Deterministic routing per spec §7.4.1, CBOR envelope, JSONL trace per §11.1
- SHA-256 artifact digest verification, FIFO orchestration with safety budgets
- Reference echo + trap bricks, three end-to-end test graphs, 44 unit tests

#### v0.3.1 — Benchmark Harness (2026-04-13)
- `ncp-bench` with warmup/runs/dataset/simulate-llm-ms/concurrency/cold-start
- `RuntimeContext` library pattern (load-once, execute-N), pluggable `TraceSink`
- `classifier-stub` brick + `support-routing-stubbed` graph (gate-and-escalate)
- 3 deterministic JSONL datasets (90/10, 97/3, 50/50); 32 cross-platform results
- BENCHMARK.md + COST_MODEL.md with reproducible methodology

#### v0.3.2 — Defensive Publication (2026-04-14)
- Apache-2.0 + NOTICE + DCO + No-CLA governance finalized
- Zenodo DOI archive (citable prior art)
- DCO enforced as required status check on `main`
- README finalized with DOI/CI/license/release badges + headline bench numbers

### Phase summary

- **Phase 1 — Spec + Validator:** COMPLETE (see `v0.2.3` above)
- **Phase 2 — Reference Runtime + Bench:** COMPLETE (see `v0.3.0`–`v0.3.1` above)

### Deferred (not yet "done")

- Replay (spec §12.2) → Phase 3B.2
- Runtime intrinsics (spec §6.6.2) → Phase 3B.6
- Carry state lifecycle → Phase 3B.5
- Graph refs / slot resolution → Phase 3B.4
- Fan-in activation policies → Phase 3B.3
- Production profile hardening → Phase 3C

---

## 2) Phase 3.0 — Release Hygiene (blocks everything)

**Goal:** After this, `main` is always known-good, and every release is credible.

### Deliverables (Definition of Done)

1. **Rust CI workflow**
   - Required checks: fmt, clippy, test, smoke-run examples, WASM digest check
2. **Cross-platform matrix**
   - `cargo` jobs run on Linux + macOS + Windows
   - WASM digest verification remains Linux-only (uses `sha256sum` + bash)
3. **Docs correctness**
   - Adoption guide commands are copy/paste correct (include `run` subcommand)
4. **Citation metadata**
   - `CITATION.cff` + `.zenodo.json` (keywords, creators, license)
5. **Repo hygiene**
   - Remove/relocate stale planning docs (or freeze under `docs/history/`)
   - Remove stray directories / weird path artifacts
6. **Branch protection**
   - Require DCO + CI checks
   - Block force-push and deletion on `main`
7. **Web sign-off**
   - Enable "Require sign-off on web-based commits" (for GitHub UI edits)

### Exit criteria

- Tag a release: `v0.3.x` "Release hygiene"
- Zenodo captures the release and DOI resolves
- README displays DOI badge and CI is green on all platforms

---

## 3) Phase 3A — Integrations + Distribution (Adoption Track)

**Goal:** Use NCP inside existing agent stacks without cloning the repo.

### 3A.1 Packaging & install paths (first)

**Deliverables**
- Prebuilt binaries for linux/darwin/windows (x86_64 + aarch64 where applicable)
- `cargo install` story is stable (pick final bin name now: `ncp`)
- Docker image on GHCR (`ghcr.io/<org>/ncp:<version>`) < 50MB if possible
- Signed release artifacts

**Exit criteria**
- New user: install → run a reference graph → view trace in < 10 minutes

### 3A.2 MCP adapter (highest leverage wedge)

**Deliverables**
- `ncp-mcp-server` that exposes: **one graph = one MCP tool**
- Multi-graph support: `--graph a.yaml --graph b.yaml`
- Transport v0: stdio only.
- Future transport: Streamable HTTP (replaces legacy HTTP+SSE in current MCP spec). Out of scope for this phase.
- Example: MCP desktop-host config + end-to-end test

**Exit criteria**
- A user can drop a graph into an MCP-compatible desktop host as a tool with no glue code

### 3A.3 LangGraph wrapper (Python)

**Deliverables**
- Python package that exposes an `NCPNode` usable inside LangGraph
- Example workflow: triage via NCP → escalate to LLM
- CI integration test

### 3A.4 SDKs (only after packaging is stable)

**Python SDK**
- `pip install ncp` runtime wrapper + trace iterator
- Brick helper functions (pack/digest/sign later)

**TypeScript SDK**
- `npm i @ncp/runtime` subprocess wrapper (avoid duplicating Wasmtime early)
- Async trace iteration

### 3A.5 Brick packs + reference graphs

Ship what people actually need:
- `bricks/validators/` (schema validation, regex bounds, required fields)
- `bricks/extractors/` (email/phone/url extraction; json-path picker)
- `bricks/routers/` (threshold routers; fallback routing)
- `bricks/policy/` (allow/block, profanity gate, PII redaction regex)
- `bricks/retrieval/` (deterministic KV lookup; cache stub)

Reference graphs:
- support triage
- tool safety gateway
- lead qualification

### 3A exit criteria

- Tag `v0.4.0`
- Installable + integrated with MCP + usable example graphs

---

## 4) Phase 3B — Runtime Completeness (Correctness Track)

**Goal:** Close spec → implementation gaps with conformance coverage.

Implement in this order (prioritize "contract unlocks"):

1. **Wall-clock timeouts**
   - Enforce `max_ms` as a real deadline (not just "best effort")
2. **Replay**
   - Deterministic replay of trace with byte-equivalent assertions (trust unlock)
   - Builds on existing JSONL trace format (no spec change)
3. **Fan-in activation policies**
   - quorum/all/any/first + conformance vectors
4. **Graph refs + slots**
   - enable composable graphs and "graph libraries"
5. **Carry state lifecycle**
   - init, TTL, migration
6. **Runtime intrinsics**
   - `ncp_runtime.*` host imports behind explicit opt-in feature flags

**Exit criteria**
- Spec sections above are executable
- Conformance vectors cover each feature
- Tag `v0.4.x` as these land, with clear changelog

---

## 5) Phase 3C — Production Profile (Hardening Track)

**Goal:** Safe to run semi-hostile inputs and untrusted bricks in real services.

1. **Threat model**
   - attacker capabilities, trust boundaries, in/out of scope mitigations
2. **Wasmtime hardening**
   - explicitly disable/allow features; document why
   - enforce instance/table/stack constraints
3. **CPU fuel metering**
   - stop infinite loops deterministically
4. **Artifact trust**
   - signed brick artifacts + allowlist/trust-root mode
5. **OTel trace sink**
   - drop-in export to existing observability
6. **Benchmark representativeness**
   - "messy" datasets + repeat/aggregate mode
   - publish "numbers you can quote" as medians of repeated runs
7. **Reproducible build verification**
   - CI rebuild comparisons; document sources of nondeterminism
8. **Security policy**
   - private disclosure channel + response expectations + GH advisories enabled

**Exit criteria**
- Tag `v0.5.0` "Production profile"
- You can credibly say: "safe-by-default sandbox, bounded compute, signed artifacts optional-but-supported"

---

## 6) Phase 4 — Ecosystem + Conformance (longer-term)

1. **Conformance suite expansion**
   - ABI vectors, boundary vectors, replay vectors, negative fixtures
2. **Second runtime implementation**
   - Go or TypeScript runtime that passes conformance (credibility multiplier)
3. **Brick discovery**
   - Start federated: GitHub topic + `ncp-search` tool
   - Central registry only if adoption demands it
4. **Real LLM case study**
   - Replace `thread::sleep` with an actual provider call in one benchmark suite
   - publish latency + cost tables with reproducible scripts
5. **Optional: adaptive routing**
   - only after real trace data exists (avoid research debt early)

---

## 7) Phase dependency map

```
  3.0 (hygiene) ──┬──► 3A (adoption) ───────┬──► 4.4 (real-LLM case)
                  │                         │
                  ├──► 3B (completeness) ───┼──► 4.1 (conformance suite)
                  │                         │
                  └──► 3C (hardening) ──────┴──► 4.2 (second runtime)
                                            │
                                            └──► 4.3 (brick discovery)
```

3.0 gates everything. 3A / 3B / 3C are independent and can run in parallel
(different code paths: `tools/` + `sdks/` vs `runtime/` vs `runtime/` + docs).
Phase 4 depends on at least one of 3A/3B being mature.

---

## 8) How to choose "what next" (if you're stuck)

If the goal is **adoption** → do **3A.1 → 3A.2** first.

If the goal is **trustworthiness** → do **3B replay** + **3C threat model + fuel**.

If the goal is **credibility with enterprises** → do **3.0 hygiene** + **3C artifact trust + OTel**.
