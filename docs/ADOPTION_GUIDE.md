<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# NCP Adoption Guide
**Goal:** get from **zero → running graph → tracing → embedding** in ~30 minutes.

This guide is written for:
- Agentic AI architects evaluating whether NCP fits their stack
- Infra / platform engineers who need deterministic, auditable execution
- Developers who want a *small*, testable “micro-agent” runtime

> NCP in one sentence: **Compose sandboxed WASM “Bricks” into deterministic graphs, and route only the hard cases to an LLM.**

---

## 0) Prereqs
- Rust toolchain (repo uses a pinned workspace rust-version; use a recent stable)
- A Linux environment is ideal for tighter latency tails (benchmarks are better on Linux)
- No external services required

---

## 1) Clone + run a graph (3 minutes)

```bash
git clone https://github.com/madeinplutofabio/neural-computation-protocol.git
cd neural-computation-protocol

# Run the runtime CLI on a known-good example
cargo run -p ncp-runtime --bin ncp --   examples/graphs/echo-pipeline/graph.yaml   --input examples/graphs/echo-pipeline/sample.json
```

You should see a JSON output printed to stdout.

---

## 2) Understand the moving parts (2 minutes)

- **Brick**: a sandboxed WASM module that implements the NCP ABI (`alloc/free/invoke`).
- **Graph**: nodes (bricks) + edges (routing + mapping).
- **Runtime**: loads graph + bricks, builds CBOR envelopes, invokes bricks, routes edges, emits traces.

Key folders:
- `runtime/` – reference runtime (Rust + Wasmtime)
- `bricks/` – reference bricks compiled to WASM
- `examples/graphs/` – graphs you can run immediately
- `examples/bricks/` – brick manifests + WASM artifacts
- `bench/` – benchmark harness + datasets + results
- `spec/` and `schemas/` – protocol spec and JSON Schemas

---

## 3) Run the “hybrid routing” demo (5 minutes)

This graph models a production pattern:
- Fast deterministic gate (classifier)
- Escalate only when confidence is low

```bash
# Positive: no escalation (fast path)
cargo run -p ncp-runtime --bin ncp --   examples/graphs/support-routing-stubbed/graph.yaml   --input examples/graphs/support-routing-stubbed/sample-positive.json

# Negative: escalation (slow path)
cargo run -p ncp-runtime --bin ncp --   examples/graphs/support-routing-stubbed/graph.yaml   --input examples/graphs/support-routing-stubbed/sample.json
```

Optional: show per-step diagnostics
```bash
cargo run -p ncp-runtime --bin ncp --   examples/graphs/support-routing-stubbed/graph.yaml   --input examples/graphs/support-routing-stubbed/sample.json   --verbose
```

---

## 4) Turn on tracing (3 minutes)

Tracing emits JSONL records with:
- per-step provenance (trigger edge/node/step)
- SHA-256 hashes
- latency per invoke
- result type

```bash
cargo run -p ncp-runtime --bin ncp --   examples/graphs/echo-chain/graph.yaml   --input examples/graphs/echo-chain/sample.json   --trace trace.jsonl
```

Inspect:
```bash
head -n 5 trace.jsonl
```

---

## 5) Benchmarks you can cite (5 minutes)

### 5.1 Pure runtime overhead
```bash
cargo run --release -p ncp-runtime --bin ncp-bench --   examples/graphs/echo-pipeline/graph.yaml   --input examples/graphs/echo-pipeline/sample.json   --warmup 500 --runs 20000   --output bench/results/my-echo-pipeline.json
```

### 5.2 “LLM latency dominates” demo (simulated I/O)
This uses harness-level sleep to model I/O-bound LLM calls without vendor deps.

```bash
cargo run --release -p ncp-runtime --bin ncp-bench --   examples/graphs/echo-pipeline/graph.yaml   --input examples/graphs/echo-pipeline/sample.json   --warmup 50 --runs 1000   --simulate-llm-ms 200 --llm-brick-pattern echo   --output bench/results/my-llm-only-baseline.json
```

### 5.3 Mixed workload: measured 90/10 dataset
```bash
cargo run --release -p ncp-runtime --bin ncp-bench --   examples/graphs/support-routing-stubbed/graph.yaml   --dataset bench/datasets/support-routing-90-10.jsonl   --warmup 100 --runs 1000   --simulate-llm-ms 200 --llm-brick-pattern echo   --output bench/results/my-90-10.json
```

**What to report in docs/marketing:**
- `p_llm_requests` (measured escalation rate)
- mean latency vs baseline mean
- p50/p95 to show “fast majority + slow tail”

---

## 6) Embed NCP in a Rust service (10 minutes)

If you want to call the runtime from your own Rust app (HTTP server, worker, agent framework),
use the library API (`RuntimeContext`) rather than shelling out to the CLI.

### Example: run a graph in-process

```rust
use std::path::Path;
use ncp_runtime::{RuntimeContext, ExecuteOptions, ExecuteHooks};
use ncp_runtime::trace::NullTrace;

fn main() -> anyhow::Result<()> {
    // Load once (compile once)
    let ctx = RuntimeContext::load(
        Path::new("examples/graphs/echo-pipeline/graph.yaml"),
        Path::new("examples/bricks"),
        None,
    )?;

    let input: serde_json::Value = serde_json::json!({ "input": { "text": "hello", "number": 1 } });

    // Execute many times (cheap)
    let mut tracer = NullTrace;
    let mut hooks = ExecuteHooks::default();
    let opts = ExecuteOptions::default();

    let report = ctx.execute(&input, &mut tracer, &mut hooks, &opts)?;
    assert!(!report.terminals.is_empty());
    Ok(())
}
```

### Why embedding matters
- Phase 3 integrations (MCP / LangGraph) want a callable API.
- You avoid process overhead, and you can manage caching/metrics yourself.

---

## 7) How teams typically adopt NCP (the realistic path)

### Step A — Start with a “gate + escalate” graph
- deterministic validator (schema, regex, keyword, rules)
- lightweight classifier (WASM inference or heuristic stub)
- on_error / low-confidence routes to an LLM node

### Step B — Measure `p_llm` on your real dataset
- run with tracing enabled
- compute escalation rate and LLM calls per request
- track quality metrics (acceptance rate, human overrides)

### Step C — Replace stubs with real bricks
- sentiment, classification, PII detection, policy checks, formatting
- only then: introduce real LLM bricks (in Phase 3/4 via adapters)

---

## 8) Where NCP fits vs common agent stacks

NCP is not trying to replace:
- planners, memory systems, multi-agent messaging, orchestration UIs

It **does** replace or harden:
- the “every request calls an LLM” baseline
- fragile prompt-chains where control flow is implicit
- un-auditable agent runs with no provenance

**Good fit:**
- high-volume support triage
- compliance / policy gates before an action
- deterministic preprocessing (normalize → validate → classify → route)

---

## 9) Production readiness (what Phase 2 does and doesn’t guarantee)

Phase 2 reference runtime is a strong base for evaluation and internal pilots:
- deterministic routing + mapping
- sandboxed WASM invokes
- trace output and safety budgets
- benchmark harness with dataset + concurrency + cold-start modes

But **production hardening** typically needs:
- operational packaging (service mode, deployment guides)
- stronger artifact trust chain / signing
- real LLM adapters + token accounting
- threat model + security review checklist

Those land in Phase 3/3C and Phase 4.

---

## 10) Next contribution opportunities (high-leverage)

If you want to contribute something *immediately useful*:
- Add a new deterministic brick (PII scrubber, validator, router gate)
- Add a dataset + benchmark result for a real workload pattern
- Improve docs: “Graph patterns” (gate+escalate, fan-out, quorum, retries)

---

## Quick links
- Benchmarks: `BENCHMARK.md`
- Cost model: `COST_MODEL.md`
- Roadmap: `docs/ROADMAP.md`
- Runtime: `runtime/`
- Examples: `examples/graphs/`

