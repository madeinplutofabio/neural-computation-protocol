<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# NCP Cost Model

A parameterized model for estimating per-request cost savings when routing
deterministic work through NCP brick graphs instead of sending every request
to an LLM.

## Purpose

This model helps estimate **when NCP graphs are cost-effective** relative to
an LLM-only architecture. It does not hardcode vendor pricing — you plug in
your own numbers. The goal is a framework for reasoning about cost, not a
sales claim.

## Definitions

| Term | Meaning |
|---|---|
| **Request** | One full graph execution: entry node to terminal(s) |
| **Step** | One brick invocation within a request |
| **S** | Average steps per request (from traces or bench) |
| **p_llm** | Fraction of requests that route to an LLM brick |
| **k_llm** | Average LLM calls per request when escalated |
| **C_brick_step** | Compute cost of one brick invocation step |
| **C_llm_call** | Average cost of one LLM call (tokens x price) |

## Architecture Comparison

### Architecture A: LLM-only

Every request calls an LLM. No deterministic pre-filtering.

```
C_A = k_A * C_llm_call
```

Where `k_A` = average LLM calls per request (often 1).

### Architecture B: NCP hybrid

Requests enter a brick graph. Deterministic bricks handle what they can
(sentiment, classification, validation, formatting). Only requests that
require judgment escalate to an LLM via routing.

```
C_B = S * C_brick_step + p_llm * k_llm * C_llm_call
```

### Savings ratio

```
Savings = C_A / C_B
```

When `p_llm` is small and `C_brick_step << C_llm_call`, savings scale
roughly as `1 / p_llm`.

## How to Measure the Inputs

### C_brick_step (from benchmarks)

Phase 2 benchmarks show **~27–30 µs p50 per step** with tracing disabled
(`NullTrace`). This step time includes envelope build, WASM invoke, CBOR
decode/validation, routing, and mapping.

To convert step time into dollars, use a simple CPU-time model:

```
C_brick_step = t_step_seconds × price_per_vCPU_second
price_per_vCPU_second = price_per_vCPU_hour / 3600
```

#### Example (explicit assumption)

Assume `price_per_vCPU_hour = $0.05` (replace with your infra cost).

- `price_per_vCPU_second = 0.05 / 3600 ≈ 1.3889e-5 $/s`
- `t_step = 30 µs = 0.000030 s`

```
C_brick_step ≈ 0.000030 × 1.3889e-5 ≈ 4.17e-10 $ per step
```

That implies:

- **1,000,000 steps ≈ $0.000417** (≈ **0.04 cents**)
- If an average request executes **S = 2 steps**, then:
  **1,000,000 requests ≈ $0.000834** (≈ **0.08 cents**)

Runtime orchestration costs fractions of a cent per million requests on
typical CPU pricing. LLM calls dominate cost.

#### Notes

- This model assumes CPU cost scales with time and that the core is
  available (no contention).
- The cited numbers are with **tracing disabled**. Tracing adds overhead.
- Real bricks (ML inference, heavier logic) add compute time on top of the
  runtime layer.

### C_llm_call (from your provider)

```
C_llm_call = tokens_in * price_in/1000 + tokens_out * price_out/1000
```

Example (illustrative, not a recommendation):
- 500 input tokens at $3/M = $0.0015
- 200 output tokens at $15/M = $0.003
- C_llm_call = $0.0045

### p_llm and k_llm (from traces)

Run your graph on a representative dataset. From traces:

```
p_llm = (requests that hit an LLM node) / (total requests)
k_llm = (total LLM invocations) / (requests that hit an LLM node)
```

Phase 2 runtime traces include `node_id` and `brick_id` per step, so you
can compute these from trace JSONL output.

## Example Scenario

| Parameter | Value | Source |
|---|---|---|
| k_A | 1 | LLM-only: one call per request |
| C_llm_call | $0.0045 | 500 in + 200 out tokens |
| S | 2.3 | Average from traces |
| C_brick_step | $0.0000000004 | ~30 us/step on $0.05/vCPU-hr VM |
| p_llm | 0.10 | 10% of requests escalate |
| k_llm | 1 | One LLM call when escalated |

```
C_A = 1 * $0.0045 = $0.0045 per request
C_B = 2.3 * $0.0000000004 + 0.10 * 1 * $0.0045 ~ $0.00045 per request
Savings = $0.0045 / $0.00045 = 10x
```

At 10% escalation rate, cost drops ~10x. At 3% escalation, ~33x.

Brick compute cost is negligible relative to LLM cost at common token sizes —
the entire savings story is driven by **p_llm**.

**The key lever is p_llm.** If your graph can handle 90%+ of requests with
deterministic bricks, the cost reduction is substantial.

## How to Get p_llm from Traces

Run your graph on a representative dataset with tracing enabled. Then extract
the escalation rate from the trace JSONL:

```bash
# Count total requests (trace records with step=0 are entry invocations)
TOTAL=$(grep '"step":0' trace.jsonl | wc -l)

# Count requests that hit an LLM brick
# (replace "org.acme.llm-" with your LLM brick_id prefix)
LLM=$(grep '"brick_id":"org.acme.llm-' trace.jsonl | wc -l)

echo "p_llm = $LLM / $TOTAL"
```

For more precise analysis, parse the JSONL and group by `trace_id` to get
per-request step counts and LLM hit rates.

## What This Model Does NOT Cover

- **Quality/accuracy trade-offs**: routing fewer requests to LLMs only saves
  money if the deterministic path produces acceptable results. Measure
  escalation acceptance rate, human override rate, or downstream metrics.
- **Latency**: brick steps are microseconds; LLM calls are 100ms–2s. The
  latency story is even more favorable than cost, but this model focuses on $.
- **Cold start**: WASM compilation at load time adds one-time latency. Amortized
  over thousands of requests, this is negligible.
- **Multi-provider pricing**: if you use different LLMs for different nodes,
  extend `C_llm_call` per node type.

## How to Validate

1. Run your graph on a representative dataset with tracing enabled
2. Extract `p_llm`, `k_llm`, `S` from traces
3. Plug in your provider's token pricing
4. Compare `C_A` vs `C_B`
5. Sanity-check: does the savings ratio match your intuition about the
   workload's complexity distribution?

The model is only as good as `p_llm`. If you don't know your escalation rate
yet, start with a conservative estimate (e.g., 30%) and measure.
