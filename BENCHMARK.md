<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# NCP Runtime Benchmarks

Microbenchmarks measuring Phase 2 reference runtime overhead: WASM invoke,
CBOR envelope/result codec, routing, field mapping, and FIFO orchestration.
Tracing disabled (`NullTrace`), verbose off, pinned trace/session IDs to
eliminate per-iteration UUID allocation.

## Scope

These benchmarks measure **runtime overhead only** — the cost of the
orchestration layer around trivially cheap echo/trap/classifier bricks. Real
workloads will add brick-internal compute time on top of these numbers.

What is measured:
- JSON input parsing and CBOR envelope construction
- WASM instantiation (ephemeral instance per invoke, compiled Module reused)
- Model B ABI: `alloc` / write / `invoke` / read / `free`
- CBOR result decoding and boundary validation
- Routing algorithm (on_success/on_error with threshold/priority)
- Field mapping with dot-path resolution and deep merge
- FIFO queue orchestration with safety budget checks

What is **not** measured:
- Trace emission (disabled via `NullTrace.enabled() == false`)
- Brick-internal computation (echo bricks return input as output;
  classifier-stub does keyword matching only)
- Disk I/O (graph + bricks loaded and compiled once before timing)
- Network I/O (none in Phase 2)

## Results — Pure Runtime Overhead

**Steps** = number of brick invocations per full graph execution (entry to
terminal). 20,000 iterations per graph. For publication, run 3 times and
report the median.

| Graph | Nodes | Edges | Steps | p50 | p95 | p99 | Mean | Min | Max |
|---|---|---|---|---|---|---|---|---|---|
| echo-pipeline | 1 | 0 | 1 | 26 us | 41 us | 63 us | 29 us | 25 us | 302 us |
| echo-chain | 2 | 1 | 2 | 60 us | 82 us | 121 us | 64 us | 58 us | 248 us |
| trap-pipeline | 1 | 0 | 1 | 25 us | 37 us | 50 us | 27 us | 25 us | 195 us |
| routing-positive | 2 | 1 | 1 | 29 us | 43 us | 55 us | 31 us | 27 us | 171 us |
| routing-negative | 2 | 1 | 2 | 58 us | 87 us | 128 us | 63 us | 56 us | 498 us |

All latencies are per full graph execution (entry to terminal), in
microseconds. Max values reflect OS scheduling jitter, not algorithmic cost.

### Interpretation

- **echo-pipeline** (1 node, 1 step): baseline single-invoke overhead.
  26 us p50 = WASM instantiate + alloc + invoke + result decode + envelope build.

- **echo-chain** (2 nodes, 2 steps): adds routing, field mapping, second invoke.
  60 us p50 ~ 2x baseline, confirming per-step overhead is ~26-30 us. The routing
  and mapping layers add negligible cost on top of WASM invocation.

- **trap-pipeline** (1 node, 1 step): measures Failure path. 25 us p50 — comparable
  to the echo happy path. Trap detection and error construction do not add
  measurable overhead.

- **routing-positive** (2 nodes, 1 step): classifier returns Success for positive
  input — no escalation, graph terminates after 1 step. 29 us p50 is consistent
  with single-step overhead despite the graph having 2 nodes and 1 edge.

- **routing-negative** (2 nodes, 2 steps): classifier returns LowConfidence for
  negative input — routes via `on_error(LOW_CONFIDENCE)` to the echo brick
  (LLM stand-in). 58 us p50 is consistent with 2-step overhead.

### Tail latency

p99 stays under 65 us for single-step graphs and under 130 us for 2-step
graphs. The max outliers (171-498 us) are consistent with OS scheduling jitter
on a desktop workstation (Windows), not algorithmic. Linux results would likely
show tighter tails.

## Results — Simulated LLM Latency

These runs model a real-world hybrid architecture where the LLM node costs
200ms per call. LLM latency is simulated using `thread::sleep(200ms)` after
matching nodes to model network-bound I/O wait (not CPU busy-wait). The
classifier brick is real WASM (keyword-based); the echo brick standing in
for an LLM receives the injected sleep at the harness level via
`ExecuteHooks.on_invoke`.

The **LLM-only baseline** uses the same harness and the same 200ms sleep to
provide a measured (not inferred) comparison point.

| Scenario | Graph | Steps | p_llm | p50 | Mean | Simulated LLM |
|---|---|---|---|---|---|---|
| LLM-only baseline | echo-pipeline | 1 | 1.0 | 200,516 us | 200,546 us | 200 ms (always hit) |
| Hybrid — positive | routing-stubbed | 1 | 0.0 | 27 us | 27 us | 200 ms (not hit) |
| Hybrid — negative | routing-stubbed | 2 | 1.0 | 200,557 us | 200,604 us | 200 ms (hit) |

**Key observations:**

- **LLM-only baseline** (every request calls the LLM): measured p50 = 200.5 ms.
  This is the comparison point for all speedup claims below.

- **Hybrid — positive** (deterministic path handles the request): p50 = 27 us.
  The LLM is never called. Speedup vs LLM-only: **~7,400x**.

- **Hybrid — negative** (request escalates to LLM): p50 = 200.6 ms. Dominated
  entirely by the simulated LLM call. Runtime overhead adds <0.1% on top of
  LLM latency.

## Claims

The following claims are supported by the benchmark data above and the
parameterized cost model in [COST_MODEL.md](COST_MODEL.md).

### Claim 1: NCP runtime overhead is negligible

Per-step overhead is ~27-30 us (p50) including WASM instantiation, CBOR
encode/decode, routing, and field mapping. For any workload where brick
compute or LLM I/O exceeds 1 ms, the runtime layer contributes <3% of
total latency.

**Evidence:** echo-pipeline p50 = 26 us, echo-chain p50 = 60 us (2 steps),
routing-positive p50 = 29 us (1 step), routing-negative p50 = 58 us (2 steps).
Overhead scales linearly with step count.

### Claim 2: Deterministic routing avoids LLM calls

When a request can be handled by deterministic bricks (keyword classifier,
validation, formatting), the LLM node is never invoked. The graph terminates
at the classifier with 0 LLM calls.

**Evidence:** routing-positive — `p_llm_requests = 0.0`, `mean_steps = 1.0`,
p50 = 29 us. The echo brick (LLM stand-in) was never reached.

### Claim 3: Latency improvement scales with escalation rate

This is a modeled mixed workload, using measured endpoints. For a workload
where `p_llm` = 10% (90% of requests handled deterministically):

```
Measured LLM-only baseline:     p50 = 200,516 us  (200.5 ms)
Measured deterministic path:    p50 =      27 us  (0.027 ms)
Measured escalation path:       p50 = 200,557 us  (200.6 ms)

Expected latency (NCP hybrid) = 0.90 * 27 us + 0.10 * 200,557 us
                               ≈ 20,080 us ≈ 20 ms

Speedup vs measured baseline   = 200,516 us / 20,080 us ≈ 10x
```

At `p_llm` = 3%, expected latency drops to ~6 ms → **~33x speedup** vs the
measured LLM-only baseline.

### Claim 4: Cost savings are proportional to escalation avoidance

From [COST_MODEL.md](COST_MODEL.md), the per-request cost formula is:

```
C_B = S * C_brick_step + p_llm * k_llm * C_llm_call
```

Since `C_brick_step` is negligible (~$4.17e-10 per step at $0.05/vCPU-hour;
see [COST_MODEL.md](COST_MODEL.md) for derivation), cost savings are driven
almost entirely by `p_llm`.

Approximate cost ratios, assuming `C_brick_step` negligible:

| p_llm | Approx. cost ratio (C_A / C_B) |
|---|---|
| 50% | ~2x |
| 10% | ~10x |
| 3% | ~33x |
| 1% | ~100x |

**Evidence:** Benchmark confirms `p_llm = 0.0` for positive input and
`p_llm = 1.0` for negative input. Real-world `p_llm` depends on the workload
and the quality of deterministic bricks.

### What these claims do NOT assert

- **Quality parity**: routing fewer requests to LLMs only saves money and
  time if the deterministic path produces acceptable results. Measure
  acceptance rate, not just speed.
- **Production latency**: these benchmarks use stub bricks. Real bricks with
  ML inference or complex logic will add per-step compute time.
- **Absolute numbers**: all results are from a single desktop machine. Cloud
  VMs, containers, and different CPUs will show different absolute numbers but
  similar relative scaling.

## Environment

| Parameter | Value |
|---|---|
| CPU | AMD Ryzen 9 5900X 12-Core (24 threads) @ 4.5 GHz |
| RAM | 128 GB DDR4 |
| OS | Windows 10 Pro 10.0.19045 |
| Rust | 1.94.0 (4a4ef493e 2026-03-02) |
| Cargo | 1.94.0 (85eff7c80 2026-01-15) |
| Wasmtime | 43.0.0 |
| Runtime | ncp-runtime 0.1.0 |
| Build | `cargo build --release` (optimized) |
| Power | Desktop workstation (no throttling) |

Results are environment-specific; treat committed numbers as a baseline for
relative comparisons, not as a performance guarantee.

## Method

1. Build in release mode: `cargo build --release`
2. Load graph and compile WASM modules once (not timed)
3. Warmup: 500 iterations (discarded) — or 50 for LLM-simulated runs
4. Timed: 20,000 iterations per graph — or 1,000 for LLM-simulated runs
5. Each iteration: full `execute()` call — envelope build through terminal output
6. Tracing: `NullTrace` (zero overhead — `enabled()` returns false, skips
   timestamp allocation and serialization entirely)
7. IDs: pinned `trace_id="bench-trace"`, `session_id="bench-session"`
   (no UUID generation per iteration)
8. Verbose: off (no stderr I/O during timed iterations)
9. LLM simulation: `--simulate-llm-ms 200` injects a `thread::sleep(200ms)`
   via `ExecuteHooks.on_invoke` after each invoke matching `--llm-brick-pattern`

## Reproduce

```bash
cargo build --release

# echo-pipeline (1 node, 1 step)
cargo run --release --bin ncp-bench -- \
  examples/graphs/echo-pipeline/graph.yaml \
  --input examples/graphs/echo-pipeline/sample.json \
  --warmup 500 --runs 20000 \
  --output bench/results/echo-pipeline.json

# echo-chain (2 nodes, 2 steps — routing + field mapping)
cargo run --release --bin ncp-bench -- \
  examples/graphs/echo-chain/graph.yaml \
  --input examples/graphs/echo-chain/sample.json \
  --warmup 500 --runs 20000 \
  --output bench/results/echo-chain.json

# trap-pipeline (1 node, failure path)
cargo run --release --bin ncp-bench -- \
  examples/graphs/trap-pipeline/graph.yaml \
  --input examples/graphs/trap-pipeline/sample.json \
  --warmup 500 --runs 20000 \
  --output bench/results/trap-pipeline.json

# routing — positive input (deterministic, no escalation)
cargo run --release --bin ncp-bench -- \
  examples/graphs/support-routing-stubbed/graph.yaml \
  --input examples/graphs/support-routing-stubbed/sample-positive.json \
  --warmup 500 --runs 20000 \
  --output bench/results/routing-positive.json

# routing — negative input (escalation via on_error)
cargo run --release --bin ncp-bench -- \
  examples/graphs/support-routing-stubbed/graph.yaml \
  --input examples/graphs/support-routing-stubbed/sample.json \
  --warmup 500 --runs 20000 \
  --output bench/results/routing-negative.json

# LLM-only baseline (every request hits 200ms simulated LLM)
cargo run --release --bin ncp-bench -- \
  examples/graphs/echo-pipeline/graph.yaml \
  --input examples/graphs/echo-pipeline/sample.json \
  --warmup 50 --runs 1000 \
  --simulate-llm-ms 200 --llm-brick-pattern echo \
  --output bench/results/llm-only-baseline.json

# routing — positive input with simulated 200ms LLM (LLM not hit)
cargo run --release --bin ncp-bench -- \
  examples/graphs/support-routing-stubbed/graph.yaml \
  --input examples/graphs/support-routing-stubbed/sample-positive.json \
  --warmup 50 --runs 1000 \
  --simulate-llm-ms 200 --llm-brick-pattern echo \
  --output bench/results/routing-positive-llm200.json

# routing — negative input with simulated 200ms LLM (LLM hit)
cargo run --release --bin ncp-bench -- \
  examples/graphs/support-routing-stubbed/graph.yaml \
  --input examples/graphs/support-routing-stubbed/sample.json \
  --warmup 50 --runs 1000 \
  --simulate-llm-ms 200 --llm-brick-pattern echo \
  --output bench/results/routing-negative-llm200.json
```

Machine-readable results (with environment metadata) are committed in
[`bench/results/`](bench/results/). Results are environment-specific; treat
committed numbers as a baseline, not a guarantee.

## Caveats

- These are **microbenchmarks** on trivial bricks. Production graphs with real
  ML inference, LLM calls, or heavy computation will be dominated by brick
  execution time, not runtime overhead.
- Results are from a single desktop machine running Windows. Cloud VMs,
  containers, Linux, and ARM targets will show different absolute numbers
  but similar relative scaling. Linux typically shows tighter tail latencies.
- WASM Module compilation happens once at load time and is excluded from timing.
  First-request latency in a cold-start scenario will be higher.
- The echo brick is ~14 KB WASM; larger modules may have different
  instantiation characteristics.
- The classifier-stub uses keyword matching, not ML inference. Real classifiers
  will add per-step compute time.
- LLM simulation uses `thread::sleep`, which models I/O latency honestly but
  does not capture CPU contention or token streaming behavior.
- For publication-quality numbers, run 3 times and report the median.
