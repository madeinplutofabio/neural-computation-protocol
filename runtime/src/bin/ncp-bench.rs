//! NCP runtime benchmark harness.
//!
//! Loads a graph once, compiles all bricks, then runs N iterations with
//! NullTrace + no verbose output for credible in-process timing.
//!
//! Optional: --simulate-llm-ms <ms> injects a sleep after any invoke where
//! brick_id contains --llm-brick-pattern (default "echo"). This models
//! LLM I/O latency at the harness level without modifying bricks.
//!
//! Usage:
//!   # Pure runtime overhead
//!   cargo run --release --bin ncp-bench -- \
//!     examples/graphs/echo-pipeline/graph.yaml \
//!     --input examples/graphs/echo-pipeline/sample.json \
//!     --runs 1000 --warmup 100
//!
//!   # Simulated LLM latency (200ms for "echo" brick = LLM stand-in)
//!   cargo run --release --bin ncp-bench -- \
//!     examples/graphs/support-routing-stubbed/graph.yaml \
//!     --input examples/graphs/support-routing-stubbed/sample.json \
//!     --runs 1000 --warmup 100 \
//!     --simulate-llm-ms 200 --llm-brick-pattern echo

use std::path::PathBuf;
use std::time::Instant;

use anyhow::{bail, Context, Result};
use clap::Parser;

use ncp_runtime::{trace::NullTrace, ExecuteHooks, ExecuteOptions, InvokeMetric, RuntimeContext};

#[derive(Parser)]
#[command(name = "ncp-bench", version, about = "NCP runtime benchmark harness")]
struct Args {
    /// Path to the graph manifest (YAML)
    graph: PathBuf,

    /// Path to the JSON input file
    #[arg(long, value_name = "FILE")]
    input: PathBuf,

    /// Directory containing brick subdirectories
    #[arg(long, default_value = "examples/bricks", value_name = "DIR")]
    brick_dir: PathBuf,

    /// Path to a YAML/JSON brick-map file
    #[arg(long, value_name = "FILE")]
    brick_map: Option<PathBuf>,

    /// Number of timed iterations
    #[arg(long, default_value = "1000")]
    runs: u64,

    /// Number of warmup iterations (not counted)
    #[arg(long, default_value = "100")]
    warmup: u64,

    /// Write results to a JSON file
    #[arg(long, value_name = "FILE")]
    output: Option<PathBuf>,

    /// Simulate LLM latency: sleep this many ms after each invoke matching --llm-brick-pattern
    #[arg(long, value_name = "MS")]
    simulate_llm_ms: Option<u64>,

    /// Brick ID substring that identifies "LLM" nodes for --simulate-llm-ms
    #[arg(long, default_value = "echo", value_name = "PATTERN")]
    llm_brick_pattern: String,
}

fn main() -> Result<()> {
    let args = Args::parse();

    // Load and compile once
    let ctx = RuntimeContext::load(&args.graph, &args.brick_dir, args.brick_map.as_deref())?;

    eprintln!(
        "Bench: graph '{}' ({} nodes, {} edges)",
        ctx.graph_id(),
        ctx.node_count(),
        ctx.edge_count(),
    );
    for info in ctx.resolved_bricks() {
        eprintln!(
            "  brick '{}' v{} ({} bytes, {})",
            info.brick_id, info.version, info.wasm_bytes, info.digest
        );
    }
    if let Some(ms) = args.simulate_llm_ms {
        eprintln!(
            "  LLM simulation: {}ms sleep on brick_id containing '{}'",
            ms, args.llm_brick_pattern,
        );
    }

    // Load input JSON
    let input_str = std::fs::read_to_string(&args.input)
        .with_context(|| format!("reading input file '{}'", args.input.display()))?;
    let json_input: serde_json::Value = serde_json::from_str(&input_str)
        .with_context(|| format!("parsing input JSON '{}'", args.input.display()))?;

    let opts = ExecuteOptions {
        trace_id: Some("bench-trace".to_string()),
        session_id: Some("bench-session".to_string()),
        verbose: false,
        all_terminals: false,
        ..Default::default()
    };

    let simulate_llm_ms = args.simulate_llm_ms;
    let pattern = args.llm_brick_pattern.as_str();

    // Warmup
    eprintln!("Warmup: {} iterations...", args.warmup);
    let mut tracer = NullTrace;
    for _ in 0..args.warmup {
        let mut hooks = ExecuteHooks::default();
        ctx.execute(&json_input, &mut tracer, &mut hooks, &opts)?;
    }

    // Timed runs
    eprintln!("Bench: {} iterations...", args.runs);
    let mut latencies_us: Vec<u64> = Vec::with_capacity(args.runs as usize);
    let mut total_llm_invokes: u64 = 0;
    let mut runs_with_llm: u64 = 0;
    let mut total_steps: u64 = 0;

    for _ in 0..args.runs {
        let mut run_llm_invokes: u64 = 0;

        let mut on_invoke = |metric: InvokeMetric| {
            if metric.brick_id.contains(pattern) {
                run_llm_invokes += 1;
                if let Some(ms) = simulate_llm_ms {
                    std::thread::sleep(std::time::Duration::from_millis(ms));
                }
            }
        };

        let mut hooks = ExecuteHooks {
            on_invoke: Some(&mut on_invoke),
        };

        let start = Instant::now();
        let report = ctx.execute(&json_input, &mut tracer, &mut hooks, &opts)?;
        let elapsed = start.elapsed();

        latencies_us.push(elapsed.as_micros() as u64);
        total_steps += report.total_steps;
        total_llm_invokes += run_llm_invokes;
        if run_llm_invokes > 0 {
            runs_with_llm += 1;
        }

        if report.terminals.is_empty() {
            bail!("bench run produced no terminal results");
        }
    }

    if latencies_us.is_empty() {
        bail!("no runs completed");
    }

    latencies_us.sort_unstable();

    let count = latencies_us.len() as u64;
    let total_us: u64 = latencies_us.iter().sum();
    let mean_us = total_us / count;
    let min_us = latencies_us[0];
    let max_us = latencies_us[count as usize - 1];
    let p50_us = percentile(&latencies_us, 50.0);
    let p95_us = percentile(&latencies_us, 95.0);
    let p99_us = percentile(&latencies_us, 99.0);

    let mean_steps_per_run = total_steps as f64 / count as f64;
    let llm_invokes_per_run = total_llm_invokes as f64 / count as f64;
    let p_llm_requests = runs_with_llm as f64 / count as f64;
    let k_llm = if runs_with_llm > 0 {
        total_llm_invokes as f64 / runs_with_llm as f64
    } else {
        0.0
    };

    // Print results
    eprintln!();
    eprintln!("── Results ({} runs) ──", count);
    eprintln!("  mean:  {:>8} us  ({:.3} ms)", mean_us, mean_us as f64 / 1000.0);
    eprintln!("  min:   {:>8} us  ({:.3} ms)", min_us, min_us as f64 / 1000.0);
    eprintln!("  max:   {:>8} us  ({:.3} ms)", max_us, max_us as f64 / 1000.0);
    eprintln!("  p50:   {:>8} us  ({:.3} ms)", p50_us, p50_us as f64 / 1000.0);
    eprintln!("  p95:   {:>8} us  ({:.3} ms)", p95_us, p95_us as f64 / 1000.0);
    eprintln!("  p99:   {:>8} us  ({:.3} ms)", p99_us, p99_us as f64 / 1000.0);
    eprintln!("  mean_steps/run: {:.2}", mean_steps_per_run);
    eprintln!("  total_steps:    {}", total_steps);
    eprintln!("  llm_invokes:    {}", total_llm_invokes);
    eprintln!("  runs_with_llm:  {}", runs_with_llm);
    eprintln!("  p_llm_requests: {:.4}", p_llm_requests);
    eprintln!("  k_llm:          {:.4}", k_llm);
    if let Some(ms) = simulate_llm_ms {
        eprintln!("  simulate_llm:   {} ms", ms);
    }

    // JSON output
    let results = serde_json::json!({
        "graph_id": ctx.graph_id(),
        "graph_version": ctx.graph_version(),
        "nodes": ctx.node_count(),
        "edges": ctx.edge_count(),
        "runs": count,
        "warmup": args.warmup,
        "mean_us": mean_us,
        "min_us": min_us,
        "max_us": max_us,
        "p50_us": p50_us,
        "p95_us": p95_us,
        "p99_us": p99_us,
        "total_steps": total_steps,
        "mean_steps_per_run": mean_steps_per_run,
        "llm_invokes": total_llm_invokes,
        "llm_invokes_per_run": llm_invokes_per_run,
        "runs_with_llm": runs_with_llm,
        "p_llm_requests": p_llm_requests,
        "k_llm": k_llm,
        "simulate_llm_ms": simulate_llm_ms,
        "llm_brick_pattern": args.llm_brick_pattern,
        "runtime_version": ncp_runtime::RUNTIME_VERSION,
        "wasmtime_version": ncp_runtime::WASMTIME_MAJOR,
        "timestamp_utc": ncp_runtime::now_rfc3339(),
    });

    if let Some(path) = &args.output {
        std::fs::write(path, serde_json::to_string_pretty(&results)?)?;
        eprintln!("Results written to {}", path.display());
    } else {
        println!("{}", serde_json::to_string_pretty(&results)?);
    }

    Ok(())
}

fn percentile(sorted: &[u64], pct: f64) -> u64 {
    if sorted.is_empty() {
        return 0;
    }
    let idx = ((pct / 100.0) * (sorted.len() - 1) as f64).round() as usize;
    sorted[idx.min(sorted.len() - 1)]
}
