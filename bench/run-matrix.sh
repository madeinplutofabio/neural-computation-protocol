#!/usr/bin/env bash
# NCP benchmark matrix — run all configs and save results.
# Usage: ./bench/run-matrix.sh [RESULTS_DIR]
# Default RESULTS_DIR: bench/results

set -euo pipefail

RESULTS="${1:-bench/results}"
mkdir -p "$RESULTS"

echo "=== Building release ==="
cargo build --release

echo ""
echo "=== Pure runtime overhead (20k iterations) ==="

cargo run --release --bin ncp-bench -- \
  examples/graphs/echo-pipeline/graph.yaml \
  --input examples/graphs/echo-pipeline/sample.json \
  --warmup 500 --runs 20000 \
  --output "$RESULTS/echo-pipeline.json"

cargo run --release --bin ncp-bench -- \
  examples/graphs/echo-chain/graph.yaml \
  --input examples/graphs/echo-chain/sample.json \
  --warmup 500 --runs 20000 \
  --output "$RESULTS/echo-chain.json"

cargo run --release --bin ncp-bench -- \
  examples/graphs/trap-pipeline/graph.yaml \
  --input examples/graphs/trap-pipeline/sample.json \
  --warmup 500 --runs 20000 \
  --output "$RESULTS/trap-pipeline.json"

cargo run --release --bin ncp-bench -- \
  examples/graphs/support-routing-stubbed/graph.yaml \
  --input examples/graphs/support-routing-stubbed/sample-positive.json \
  --warmup 500 --runs 20000 \
  --output "$RESULTS/routing-positive.json"

cargo run --release --bin ncp-bench -- \
  examples/graphs/support-routing-stubbed/graph.yaml \
  --input examples/graphs/support-routing-stubbed/sample.json \
  --warmup 500 --runs 20000 \
  --output "$RESULTS/routing-negative.json"

echo ""
echo "=== Simulated LLM latency (1k iterations) ==="

cargo run --release --bin ncp-bench -- \
  examples/graphs/echo-pipeline/graph.yaml \
  --input examples/graphs/echo-pipeline/sample.json \
  --warmup 50 --runs 1000 \
  --simulate-llm-ms 200 --llm-brick-pattern echo \
  --output "$RESULTS/llm-only-baseline.json"

cargo run --release --bin ncp-bench -- \
  examples/graphs/support-routing-stubbed/graph.yaml \
  --input examples/graphs/support-routing-stubbed/sample-positive.json \
  --warmup 50 --runs 1000 \
  --simulate-llm-ms 200 --llm-brick-pattern echo \
  --output "$RESULTS/routing-positive-llm200.json"

cargo run --release --bin ncp-bench -- \
  examples/graphs/support-routing-stubbed/graph.yaml \
  --input examples/graphs/support-routing-stubbed/sample.json \
  --warmup 50 --runs 1000 \
  --simulate-llm-ms 200 --llm-brick-pattern echo \
  --output "$RESULTS/routing-negative-llm200.json"

echo ""
echo "=== Mixed synthetic workloads (1k iterations) ==="

cargo run --release --bin ncp-bench -- \
  examples/graphs/support-routing-stubbed/graph.yaml \
  --dataset bench/datasets/support-routing-90-10.jsonl \
  --warmup 50 --runs 1000 \
  --simulate-llm-ms 200 --llm-brick-pattern echo \
  --output "$RESULTS/mixed-90-10-llm200.json"

cargo run --release --bin ncp-bench -- \
  examples/graphs/support-routing-stubbed/graph.yaml \
  --dataset bench/datasets/support-routing-97-3.jsonl \
  --warmup 50 --runs 1000 \
  --simulate-llm-ms 200 --llm-brick-pattern echo \
  --output "$RESULTS/mixed-97-3-llm200.json"

cargo run --release --bin ncp-bench -- \
  examples/graphs/support-routing-stubbed/graph.yaml \
  --dataset bench/datasets/support-routing-50-50.jsonl \
  --warmup 50 --runs 1000 \
  --simulate-llm-ms 200 --llm-brick-pattern echo \
  --output "$RESULTS/mixed-50-50-llm200.json"

echo ""
echo "=== Done. Results in $RESULTS/ ==="
ls -la "$RESULTS/"
