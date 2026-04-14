<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

<p align="center">
  <img src="NCP-logo.png" alt="NCP Logo" width="200" />
</p>

<h1 align="center">NCP — Neural Computation Protocol</h1>

<p align="center">
  <strong>Composable, auditable micro-agent graphs for agentic AI systems.</strong><br/>
  Route cheap deterministic work to WASM “Bricks”. Escalate only when needed.
</p>

<p align="center">
  <a href="https://github.com/madeinplutofabio/neural-computation-protocol/actions/workflows/validate.yml">
    <img alt="CI" src="https://github.com/madeinplutofabio/neural-computation-protocol/actions/workflows/validate.yml/badge.svg" />
  </a>
  <a href="https://opensource.org/licenses/Apache-2.0">
    <img alt="License" src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" />
  </a>
[![DOI](https://zenodo.org)](https://zenodo.org)
  <a href="https://github.com/madeinplutofabio/neural-computation-protocol/releases">
    <img alt="Release" src="https://img.shields.io/github/v/release/madeinplutofabio/neural-computation-protocol?display_name=tag&include_prereleases" />
  </a>
  <a href="https://github.com/madeinplutofabio/neural-computation-protocol/stargazers">
    <img alt="Stars" src="https://img.shields.io/github/stars/madeinplutofabio/neural-computation-protocol?style=social" />
  </a>
</p>

---

**Docs:** [Adoption guide](docs/ADOPTION_GUIDE.md) · [Benchmarks](BENCHMARK.md) · [Cost model](COST_MODEL.md) · [Roadmap](docs/ROADMAP.md) · [Spec](spec/ncp-v0.2.3.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

**Status:** Protocol v0.2.3 + validator are stable. The Phase 2 runtime is a reference implementation (fast, deterministic, benchmarked). Phase 3 focuses on integrations (MCP/LangGraph) and distribution.

## What is NCP?

NCP is an open protocol + reference implementation for building **agentic systems from small, sandboxed WASM functions** (**Bricks**) wired into **directed graphs**.

Instead of “LLM for everything”, you build a graph that:

- runs **cheap deterministic steps** first (validation, parsing, routing, extraction, policy checks)
- escalates to **expensive / slow steps** only when needed (LLMs, retrieval, heavy ML inference)
- emits **traceable, replayable** execution metadata (hashes + provenance)

### Core concepts

| Concept | What it is |
|---|---|
| **Brick** | Pure-functional WASM computation unit. No filesystem. No network. No ambient authority. Deterministic by default. |
| **Graph** | Bricks connected by typed edges with routing policies (success/error), threshold gating, and field mapping. |
| **Runtime** | The executor: sandboxes Bricks, enforces resource limits, routes signals deterministically, and produces traces. |

**Core insight:** Bricks are commodity (reusable, swappable). Graphs are product (your topology + thresholds + weights).

---

## Why teams use NCP

- **Cost**: avoid paying LLM tokens for requests your deterministic path can handle.
- **Latency**: keep most requests in microseconds; reserve 100ms–2s paths for the hard tail.
- **Auditability**: every invoke can be traced (hashes, step counters, trigger provenance).
- **Safety**: WASM sandbox + explicit limits; no prompt-injection surface inside deterministic bricks.
- **Composability**: swap bricks / rewire graphs without changing application code.

### Who this is for

If you ship “agentic” workflows in production and your **latency or LLM bill keeps climbing**, NCP is for:

- **CEOs/CTOs**: reduce inference spend and make behavior more predictable.
- **Platform/AI engineers**: build a fast deterministic path + a controlled LLM tail.
- **SRE/DevOps**: tighten limits, reduce surprise load, and get replayable traces for incidents.
- **Product teams**: iterate by rewiring graphs instead of rewriting code.

### When NCP is a good fit

- you have **high volume** requests with a “long tail” that truly needs an LLM
- you want **repeatable / testable** agent behavior (deterministic fast path)
- you need **clear boundaries**: what can run, for how long, with how much memory

---

## Benchmarks

Benchmarks are in [`BENCHMARK.md`](BENCHMARK.md) with full methodology, raw JSON, and reproduction commands.

**In practice:** if you can keep ~90% of requests off the LLM, your average latency and cost drop ~10×. The benchmark suite proves this curve end-to-end (mixed datasets + simulated 200ms LLM).

> **Important:** the **µs numbers** are *runtime overhead* (fast path). The win comes from avoiding **ms–s** LLM calls on requests that don’t need “thinking”.

### A concrete example

Support triage:
- 90% are “boring”: password reset, invoice request, status updates → deterministic bricks handle them.
- 10% are messy: angry customers, edge cases → escalate to LLM.

That’s exactly what NCP is built for: a **cheap, deterministic fast path** + an **explicit escalation path**.

Highlights below use the **Linux run** in `bench/results/linux/`:

### 1) Runtime overhead is tiny

Single-step graph (**echo-pipeline**) p50 is **15µs**.
Two-step graph (**echo-chain**) p50 is **34µs**.

That includes: CBOR envelope build + WASM invoke + result decode + routing/mapping overhead.

### 2) “LLM avoidance” turns into real speedups

We measure a synthetic mixed workload where an LLM call costs **200ms** (simulated by `thread::sleep(200ms)` after matching the “LLM brick”). This models network-bound LLM calls without vendor dependencies.

- **LLM-only baseline** (every request hits the 200ms “LLM”): mean **200.2ms**
- **Hybrid 90/10** (90% handled deterministically): mean **20.0ms** (**~10× lower**)
- **Hybrid 97/3** (97% handled deterministically): mean **6.0ms** (**~33× lower**)

These results are measured end-to-end by cycling a dataset (`--dataset`) so the latency distribution includes both fast-path and slow-path requests in one run.

**Cost follows the same curve**: if you avoid LLM calls, you avoid LLM spend. See [`COST_MODEL.md`](COST_MODEL.md).

---

## Quick start (runtime)

Prereqs: **Rust 1.94+**.

New here? Start with **docs/ADOPTION_GUIDE.md** — what to build first, how to choose bricks, how to design “fast path vs LLM path”, and how to deploy NCP in a service.

```bash
git clone https://github.com/madeinplutofabio/neural-computation-protocol.git
cd neural-computation-protocol

# Build the reference runtime
cargo build -p ncp-runtime --release

# Run a graph (2-step example: echo_a -> echo_b with field mapping)
cargo run -p ncp-runtime --release -- run examples/graphs/echo-chain/graph.yaml \
  --input examples/graphs/echo-chain/sample.json

# Optional: write trace to file (JSONL)
cargo run -p ncp-runtime --release -- run examples/graphs/echo-pipeline/graph.yaml \
  --input examples/graphs/echo-pipeline/sample.json --trace trace.jsonl
```

### Benchmark quick start

```bash
# Pure runtime overhead
cargo run -p ncp-runtime --release --bin ncp-bench -- \
  examples/graphs/echo-pipeline/graph.yaml \
  --input examples/graphs/echo-pipeline/sample.json \
  --warmup 500 --runs 20000

# Mixed workload + simulated LLM latency
cargo run -p ncp-runtime --release --bin ncp-bench -- \
  examples/graphs/support-routing-stubbed/graph.yaml \
  --dataset bench/datasets/support-routing-90-10.jsonl \
  --warmup 100 --runs 1000 \
  --simulate-llm-ms 200 --llm-brick-pattern echo
```

---

## Specification + tooling

- **Protocol version:** v0.2.3
- **Canonical spec:** `spec/ncp-v0.2.3.md`
- **JSON Schemas:** `schemas/` (Draft 2020-12)
- **Validator:** `tools/ncp-validate/`
- **Reference runtime:** `runtime/` (Rust + Wasmtime 43)
- **Conformance vectors:** `conformance/`

### Validator quick start

Prereqs: Node.js 18+.

```bash
cd tools/ncp-validate
npm install
npm run build

# Validate a brick or graph manifest
node dist/cli.js brick ../../examples/bricks/echo/manifest.yaml
node dist/cli.js graph ../../examples/graphs/echo-chain/graph.yaml
```

---

## Repository structure

```
spec/            Protocol specification (Markdown + PDF releases)
schemas/         JSON Schema for all NCP structures
runtime/         Reference runtime (Rust) + bench harness
bricks/          Reference brick implementations (Rust -> WASM)
examples/        Brick + graph manifests, fixtures, and demo graphs
bench/           Datasets + machine-readable results (Windows + Linux)
tools/           Validator CLI (ncp-validate)
conformance/     Test vectors for runtime implementors
docs/            Roadmap and design notes
```

---

## Roadmap

High-level roadmap lives in [`docs/ROADMAP.md`](docs/ROADMAP.md).

If you’re evaluating NCP today:
- Phase 1 (Spec + Validator): ✅ complete
- Phase 2 (Reference Runtime + Benchmarking): ✅ complete
- Phase 3 (Integrations + distribution): 🚧 in progress

---

## Get involved

If you find NCP useful, please consider giving us a star on GitHub: it helps attract more security experts and framework authors into the community.

If you want NCP to be useful in real systems, the best help is:

- **Adapters / integrations** (MCP tool server, LangGraph node wrapper)
- **Brick packs** (reusable deterministic bricks: validators, extractors, routers)
- **Conformance** (vectors + cross-runtime test harness)
- **Docs** (clear patterns, examples, and “how to adopt” guides)

Start here:
- `CONTRIBUTING.md`
- `SECURITY.md`

---

## License

Apache-2.0 — see `LICENSE` and `NOTICE`.

---

Maintained by [![Linkedin](https://i.sstatic.net/gVE0j.png) @fmsalvadori](https://www.linkedin.com/in/fmsalvadori/)
&nbsp;
[![GitHub](https://i.sstatic.net/tskMh.png) MadeInPluto](https://github.com/madeinplutofabio)
