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
| echo-pipeline | 1 | 0 | 1 | 27 us | 35 us | 43 us | 28 us | 26 us | 219 us |
| echo-chain | 2 | 1 | 2 | 59 us | 88 us | 116 us | 65 us | 57 us | 235 us |
| trap-pipeline | 1 | 0 | 1 | 29 us | 41 us | 62 us | 31 us | 27 us | 173 us |
| routing-positive | 2 | 1 | 1 | 27 us | 39 us | 45 us | 28 us | 26 us | 163 us |
| routing-negative | 2 | 1 | 2 | 57 us | 83 us | 117 us | 61 us | 55 us | 298 us |

All latencies are per full graph execution (entry to terminal), in
microseconds. Max values reflect OS scheduling jitter, not algorithmic cost.

### Interpretation

- **echo-pipeline** (1 node, 1 step): baseline single-invoke overhead.
  27 us p50 = WASM instantiate + alloc + invoke + result decode + envelope build.

- **echo-chain** (2 nodes, 2 steps): adds routing, field mapping, second invoke.
  59 us p50 ~ 2x baseline, confirming per-step overhead is ~27-30 us. The routing
  and mapping layers add negligible cost on top of WASM invocation.

- **trap-pipeline** (1 node, 1 step): measures Failure path. 29 us p50 — comparable
  to the echo happy path. Trap detection and error construction do not add
  measurable overhead.

- **routing-positive** (2 nodes, 1 step): classifier returns Success for positive
  input — no escalation, graph terminates after 1 step. 27 us p50 is consistent
  with single-step overhead despite the graph having 2 nodes and 1 edge.

- **routing-negative** (2 nodes, 2 steps): classifier returns LowConfidence for
  negative input — routes via `on_error(LOW_CONFIDENCE)` to the echo brick
  (LLM stand-in). 57 us p50 is consistent with 2-step overhead.

### Tail latency

p99 stays under 62 us for single-step graphs and under 117 us for 2-step
graphs. The max outliers (163-298 us) are consistent with OS scheduling jitter
on a desktop workstation (Windows), not algorithmic. Linux results show tighter
tails (see cross-platform comparison below).

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
| LLM-only baseline | echo-pipeline | 1 | 1.0 | 200,511 us | 200,551 us | 200 ms (always hit) |
| Hybrid — positive | routing-stubbed | 1 | 0.0 | 32 us | 34 us | 200 ms (not hit) |
| Hybrid — negative | routing-stubbed | 2 | 1.0 | 200,556 us | 200,607 us | 200 ms (hit) |

**Key observations:**

- **LLM-only baseline** (every request calls the LLM): measured p50 = 200.5 ms.
  This is the comparison point for all speedup claims below.

- **Hybrid — positive** (deterministic path handles the request): p50 = 32 us.
  The LLM is never called. Speedup vs LLM-only: **~6,300x**.

- **Hybrid — negative** (request escalates to LLM): p50 = 200.6 ms. Dominated
  entirely by the simulated LLM call. Runtime overhead adds <0.1% on top of
  LLM latency.

## Results — Mixed Synthetic Workloads

These runs use `--dataset` mode: a JSONL file with a fixed mix of positive
(non-escalating) and negative (escalating) inputs, cycled deterministically.
Each iteration parses one JSON line and executes the full graph. Both
end-to-end (parse + execute) and execute-only latencies are reported;
parse overhead is negligible (<2 us difference).

This is a **measured** mixed synthetic workload, not a modeled weighted
average. The harness cycles through the dataset in fixed order, producing
a real latency distribution that includes both fast deterministic paths
and slow LLM-simulated escalations in a single run.

| Dataset | Lines | p_llm (measured) | Exec mean | Exec p50 | Exec p95 | Exec p99 | Speedup vs baseline |
|---|---|---|---|---|---|---|---|
| 97/3 | 100 | 0.03 | 6,050 us (6.1 ms) | 31 us | 79 us | 200,661 us | **~33x** |
| 90/10 | 100 | 0.10 | 20,088 us (20.1 ms) | 32 us | 200,486 us | 200,947 us | **~10x** |
| 50/50 | 100 | 0.50 | 100,308 us (100.3 ms) | 200,104 us | 200,970 us | 201,095 us | **~2x** |

**Baseline (LLM-only):** mean = 200,551 us (200.6 ms), measured with the same
harness and `--simulate-llm-ms 200`. Speedup = baseline mean / dataset exec mean.

**Key observations:**

- Speedup scales as **~1/p_llm**, confirmed by measurement: 33x at 3%, 10x at 10%, 2x at 50%.
- At 3% escalation, **p95 = 79 us** — 95% of requests complete in under 0.1 ms
  (only the ~3% escalation tail is slow).
- At 10% escalation, **p50 = 32 us** but **p95 = 200 ms** — the 10% tail is dominated by LLM latency.
- Parse overhead is negligible: e2e and exec stats differ by <2 us across all datasets.

## Claims

The following claims are supported by the benchmark data above and the
parameterized cost model in [COST_MODEL.md](COST_MODEL.md).

### Claim 1: NCP runtime overhead is negligible

Per-step overhead is ~27-30 us (p50) including WASM instantiation, CBOR
encode/decode, routing, and field mapping. For any workload where brick
compute or LLM I/O exceeds 1 ms, the runtime layer contributes <3% of
total latency.

**Evidence:** echo-pipeline p50 = 27 us, echo-chain p50 = 59 us (2 steps),
routing-positive p50 = 27 us (1 step), routing-negative p50 = 57 us (2 steps).
Overhead scales linearly with step count.

### Claim 2: Deterministic routing avoids LLM calls

When a request can be handled by deterministic bricks (keyword classifier,
validation, formatting), the LLM node is never invoked. The graph terminates
at the classifier with 0 LLM calls.

**Evidence:** routing-positive — `p_llm_requests = 0.0`, `mean_steps = 1.0`,
p50 = 27 us. The echo brick (LLM stand-in) was never reached.

### Claim 3: Latency improvement scales with escalation rate

Measured on mixed synthetic workloads (deterministic dataset order, 200ms
simulated LLM), using the same harness and baseline:

| p_llm | Measured exec mean | Speedup vs baseline |
|---|---|---|
| 3% | 6.1 ms | **~33x** |
| 10% | 20.1 ms | **~10x** |
| 50% | 100.3 ms | **~2x** |

Speedup scales as ~1/p_llm. These are direct measurements, not modeled
weighted averages — the harness ran each dataset mix end-to-end and
computed the latency distribution from actual iteration timings.

**Corroboration:** the modeled weighted average (0.90 × 34 us + 0.10 ×
200,607 us ≈ 20,091 us) matches the measured 90/10 mean of 20,088 us
to within 0.01%, confirming harness correctness.

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
- **Absolute numbers**: results vary by platform. Linux (WSL2) shows ~1.8x
  faster per-step overhead than Windows on the same hardware (see cross-
  platform comparison below). Cloud VMs, containers, and different CPUs will
  show different absolute numbers but similar relative scaling.

## Results — Cold Start

Cold-start time measures the full load + compile path: reading graph and brick
manifests, loading WASM bytes, verifying SHA-256 digests, and compiling WASM
modules via Wasmtime. Measured with `--cold-start --warmup 0 --runs 1`.

| Graph | Bricks | WASM total | Windows | Linux (WSL2) |
|---|---|---|---|---|
| echo-pipeline | 1 (echo, 14 KB) | 14 KB | 15.7 ms | **7.8 ms** |
| support-routing-stubbed | 2 (classifier-stub 15 KB + echo 14 KB) | 29 KB | 25.9 ms | **17.6 ms** |

Cold start scales roughly with the number and size of WASM modules. Linux is
~2x faster on cold start (tighter filesystem + compilation overhead). For
comparison, a single step after warmup takes ~27 us (Windows) / ~15 us (Linux)
— cold start is hundreds of warm invokes, but still well under 30 ms even for
a 2-brick graph.

## Results — Concurrency Throughput

Throughput measured with the echo-pipeline graph (1 node, 1 step), 10,000 total
runs distributed across N worker threads. Each thread gets its own WASM instance
(via `RuntimeContext::execute`, which is `&self` / Send+Sync). The compiled
Module is shared via `Arc<RuntimeContext>`.

### Windows

| Threads | Mean latency | Wall time | Throughput |
|---|---|---|---|
| 1 | 29 us | 301 ms | **33,181 req/s** |
| 2 | 49 us | 256 ms | **39,027 req/s** |
| 4 | 112 us | 284 ms | **35,159 req/s** |

### Linux (WSL2)

| Threads | Mean latency | Wall time | Throughput |
|---|---|---|---|
| 1 | 16 us | 170 ms | **58,870 req/s** |
| 2 | 26 us | 137 ms | **73,199 req/s** |
| 4 | 60 us | 153 ms | **65,218 req/s** |

**Key observations:**

- Linux single-threaded throughput is ~1.8x Windows (59k vs 33k req/s),
  consistent with the per-step latency ratio.
- 2 threads is the sweet spot on both platforms: ~1.18x on Windows (39k),
  ~1.24x on Linux (73k). Per-request latency increases due to contention.
- 4 threads shows diminishing returns on this workload, as the trivial echo
  brick does not leave enough headroom for parallelism — thread coordination
  overhead dominates.
- For I/O-bound workloads (real LLM calls), concurrency gains will be much
  larger since threads can overlap LLM wait times.

## Cross-Platform Comparison

All runs on the same hardware (Ryzen 9 5900X), same Rust 1.94.0 / Wasmtime 43,
same release build. Linux runs via WSL2 (Ubuntu 24.04).

### Pure runtime overhead (p50, microseconds)

| Graph | Steps | Windows | Linux (WSL2) |
|---|---|---|---|
| echo-pipeline | 1 | 27 us | **15 us** |
| echo-chain | 2 | 59 us | **34 us** |
| trap-pipeline | 1 | 29 us | **14 us** |
| routing-positive | 1 | 27 us | **16 us** |
| routing-negative | 2 | 57 us | **35 us** |

Linux per-step overhead is ~15-16 us (vs ~27-29 us on Windows), a ~1.8x
improvement. This is consistent with tighter syscall overhead and scheduler
granularity on Linux.

### Tail latency (p99, microseconds)

| Graph | Windows p99 | Linux p99 |
|---|---|---|
| echo-pipeline | 43 us | **29 us** |
| echo-chain | 116 us | **61 us** |
| trap-pipeline | 62 us | **26 us** |
| routing-positive | 45 us | **33 us** |
| routing-negative | 117 us | **63 us** |

Linux tails are ~2x tighter than Windows, as expected from scheduler
differences.

### Mixed synthetic workloads (exec mean, with 200ms simulated LLM)

| Dataset | p_llm | Windows mean | Linux mean | Speedup vs baseline |
|---|---|---|---|---|
| 97/3 | 0.03 | 6,050 us | 6,024 us | **~33x** |
| 90/10 | 0.10 | 20,088 us | 20,039 us | **~10x** |
| 50/50 | 0.50 | 100,308 us | 100,122 us | **~2x** |

Mixed-workload means are nearly identical across platforms because they are
dominated by the 200ms simulated LLM sleep. The speedup ratios hold
regardless of OS.

## Environment

### Windows (primary)

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

### Linux (WSL2)

| Parameter | Value |
|---|---|
| CPU | Same (AMD Ryzen 9 5900X, shared with host) |
| RAM | Same (shared with host) |
| OS | Ubuntu 24.04.1 LTS on WSL2 (kernel 5.10.16.3-microsoft-standard-WSL2) |
| Rust | 1.94.0 |
| Wasmtime | 43.0.0 |
| Runtime | ncp-runtime 0.1.0 |
| Build | `cargo build --release` (optimized) |

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
10. Datasets are deterministic (no shuffle) to keep results exactly reproducible;
    the dataset SHA-256 is printed by `ncp-bench` and recorded in JSON outputs
11. Cold-start: `--cold-start` measures load + compile time separately (graph
    and brick manifests, WASM loading, SHA-256 verification, Wasmtime compilation)
12. Concurrency: `--concurrency N` distributes runs across N worker threads
    sharing `Arc<RuntimeContext>`; reports wall time and throughput (req/s)

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

# mixed synthetic workload — 90/10 with simulated 200ms LLM
cargo run --release --bin ncp-bench -- \
  examples/graphs/support-routing-stubbed/graph.yaml \
  --dataset bench/datasets/support-routing-90-10.jsonl \
  --warmup 50 --runs 1000 \
  --simulate-llm-ms 200 --llm-brick-pattern echo \
  --output bench/results/mixed-90-10-llm200.json

# mixed synthetic workload — 97/3 with simulated 200ms LLM
cargo run --release --bin ncp-bench -- \
  examples/graphs/support-routing-stubbed/graph.yaml \
  --dataset bench/datasets/support-routing-97-3.jsonl \
  --warmup 50 --runs 1000 \
  --simulate-llm-ms 200 --llm-brick-pattern echo \
  --output bench/results/mixed-97-3-llm200.json

# mixed synthetic workload — 50/50 with simulated 200ms LLM
cargo run --release --bin ncp-bench -- \
  examples/graphs/support-routing-stubbed/graph.yaml \
  --dataset bench/datasets/support-routing-50-50.jsonl \
  --warmup 50 --runs 1000 \
  --simulate-llm-ms 200 --llm-brick-pattern echo \
  --output bench/results/mixed-50-50-llm200.json
```

# cold-start (load + compile timing)
cargo run --release --bin ncp-bench -- \
  examples/graphs/echo-pipeline/graph.yaml \
  --input examples/graphs/echo-pipeline/sample.json \
  --warmup 0 --runs 1 --cold-start \
  --output bench/results/cold-start-echo.json

cargo run --release --bin ncp-bench -- \
  examples/graphs/support-routing-stubbed/graph.yaml \
  --input examples/graphs/support-routing-stubbed/sample-positive.json \
  --warmup 0 --runs 1 --cold-start \
  --output bench/results/cold-start-routing.json

# concurrency throughput (1, 2, 4 threads)
for C in 1 2 4; do
  cargo run --release --bin ncp-bench -- \
    examples/graphs/echo-pipeline/graph.yaml \
    --input examples/graphs/echo-pipeline/sample.json \
    --warmup 500 --runs 10000 --concurrency $C \
    --output bench/results/throughput-echo-c${C}.json
done
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
- WASM Module compilation happens once at load time and is excluded from
  per-iteration timing. Cold-start is measured separately (8-26 ms for
  1-2 brick graphs depending on platform); see Results — Cold Start.
- The echo brick is ~14 KB WASM; larger modules may have different
  instantiation characteristics.
- The classifier-stub uses keyword matching, not ML inference. Real classifiers
  will add per-step compute time.
- LLM simulation uses `thread::sleep`, which models I/O latency honestly but
  does not capture CPU contention or token streaming behavior.
- For publication-quality numbers, run 3 times and report the median.
