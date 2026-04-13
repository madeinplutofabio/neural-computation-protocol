<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# NCP Cost Model

A parameterized framework for estimating **cost and latency** savings when routing
deterministic work through NCP brick graphs instead of sending every request to an LLM.

This document is deliberately **vendor-neutral**: you plug in your own token pricing and latency numbers.

If you want the measured benchmark evidence behind the claims, see:
- [`BENCHMARK.md`](BENCHMARK.md) (full methodology + raw results)

---

## What this model is (and is not)

This model helps you answer:

- “If I can avoid the LLM on X% of requests, what happens to **latency** and **spend**?”
- “Which metrics should I measure in production (p_llm, k_llm, step count)?”
- “What is the break-even point where deterministic routing is worth it?”

This model does **not** guarantee:

- accuracy / quality parity with LLM-only systems
- your exact production numbers (workload + infra matter)

---

## Definitions

| Term | Meaning |
|---|---|
| **Request** | One full graph execution: entry node → terminal node(s) |
| **Step** | One brick invocation within a request |
| **S** | Average steps per request |
| **p_llm** | Fraction of requests that hit an LLM node (escalation rate) |
| **k_llm** | Average LLM calls per escalated request |
| **C_step** | Compute cost of one deterministic step (brick invocation) |
| **C_llm** | Average cost of one LLM call (tokens × price) |
| **t_step** | Average latency of one deterministic step |
| **t_llm** | Average latency of one LLM call |

---

## Architecture comparison

### A) LLM-only architecture

Every request calls an LLM.

- **Cost:**

  ```
  C_A = k_A * C_llm
  ```

  (often `k_A = 1`)

- **Latency (mean):**

  ```
  t_A ≈ t_llm
  ```

### B) NCP hybrid architecture

Requests enter a brick graph. Deterministic bricks handle what they can.
Only a fraction escalates to an LLM.

- **Cost:**

  ```
  C_B = S * C_step + p_llm * k_llm * C_llm
  ```

- **Latency (mean):**

  ```
  t_B ≈ S * t_step + p_llm * k_llm * t_llm
  ```

---

## How to measure the inputs

### 1) Measure S (steps per request)

From runtime traces or benchmark output:

- `mean_steps_per_run` in `ncp-bench` JSON output
- or compute from trace JSONL by counting step records per request

### 2) Measure p_llm and k_llm

From traces (or simulated-bench output):

- `p_llm` = fraction of requests that hit ≥1 LLM node
- `k_llm` = LLM calls per escalated request

In `ncp-bench` with `--simulate-llm-ms`, the JSON output includes:

- `p_llm_requests`
- `k_llm`
- `llm_invokes_per_run`

### 3) Estimate C_llm (token pricing)

For a single call:

```
C_llm = tokens_in * price_in + tokens_out * price_out
```

(Use your provider’s units; e.g., $/token or $/1M tokens.)

### 4) Estimate C_step (deterministic compute)

From measured step latency `t_step` and your compute pricing.

A simple approximation:

```
C_step ≈ t_step * price_per_cpu_second
```

On modern machines, `t_step` is typically **tens of microseconds** for trivial bricks.
That makes `C_step` negligible compared to `C_llm` in most systems.

---

## Worked example (measured benchmark endpoints)

The Phase 2 benchmark suite measures:

- deterministic path latency (microseconds)
- LLM-only baseline latency (simulated 200ms I/O wait)
- mixed datasets (97/3, 90/10, 50/50) where p_llm is measured, not assumed

From `bench/results/linux/`:

- LLM-only baseline mean ≈ **200.2 ms**
- Mixed 90/10 mean ≈ **20.0 ms** (measured p_llm ≈ 0.10)
- Mixed 97/3 mean ≈ **6.0 ms** (measured p_llm ≈ 0.03)

**Latency implication (mean):** if your workload escalates 10% of the time, mean latency drops ~10× (because the LLM dominates).

**Cost implication:** if your spend is dominated by LLM calls, and you avoid 90% of them, your LLM spend drops ~10×. The deterministic runtime overhead is tiny by comparison.

---

## What to report in a production write-up

If you want credible claims, publish these:

1. `p_llm` (escalation rate) on a representative dataset
2. `k_llm` (LLM calls per escalated request)
3. `S` (mean steps per request)
4. Latency distribution (p50/p95/p99), not just mean
5. Token usage (input/output tokens per call) for cost

NCP makes (1)–(3) easy to compute from trace records.

---

## Caveats and extensions

- **Quality/accuracy matters:** Avoiding the LLM only helps if deterministic results are acceptable.
  Track acceptance rate, human override rate, downstream conversion, etc.
- **Tail latency:** Mixed workloads often have low p50 but high p95 (the “LLM tail”).
  That’s normal: most requests are fast, and the hard tail is slow.
- **Multiple LLM nodes:** If different nodes use different models, extend the formula
  with `C_llm_i` and `t_llm_i` per node.
- **Cold start:** First-run latency includes graph + WASM compile time.
  Benchmarks report `cold_start_us` when enabled.

---

## How to validate with your own workload

1. Build a graph with a clear “LLM escalation” node boundary
2. Run a representative dataset through the runtime with tracing enabled
3. Compute `p_llm`, `k_llm`, `S` from traces
4. Plug in your provider’s token pricing
5. Compare LLM-only vs hybrid

If you don’t know `p_llm` yet, start with a conservative estimate (30–50%) and measure.
