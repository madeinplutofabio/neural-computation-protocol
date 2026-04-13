//! NCP runtime benchmark harness.
//!
//! Loads a graph once, compiles all bricks, then runs N iterations with
//! NullTrace + no verbose output for credible in-process timing.
//!
//! Two input modes:
//!   --input FILE     Single JSON input, parsed once, reused every iteration.
//!                    Measures pure runtime overhead (no parse cost in timing).
//!   --dataset FILE   JSONL file (one JSON per line), parsed per iteration.
//!                    Measures end-to-end request handling including parse cost.
//!                    Outputs both execute_us and end_to_end_us latencies.
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
//!   # Mixed workload (90/10 dataset with simulated LLM)
//!   cargo run --release --bin ncp-bench -- \
//!     examples/graphs/support-routing-stubbed/graph.yaml \
//!     --dataset bench/datasets/support-routing-90-10.jsonl \
//!     --runs 1000 --warmup 100 \
//!     --simulate-llm-ms 200 --llm-brick-pattern echo

use std::path::PathBuf;
use std::time::Instant;

use anyhow::{bail, Context, Result};
use clap::Parser;
use sha2::{Digest, Sha256};

use ncp_runtime::{trace::NullTrace, ExecuteHooks, ExecuteOptions, InvokeMetric, RuntimeContext};

#[derive(Parser)]
#[command(name = "ncp-bench", version, about = "NCP runtime benchmark harness")]
struct Args {
    /// Path to the graph manifest (YAML)
    graph: PathBuf,

    /// Path to a single JSON input file (parsed once, reused every iteration)
    #[arg(long, value_name = "FILE", conflicts_with = "dataset")]
    input: Option<PathBuf>,

    /// Path to a JSONL dataset (one JSON per line, parsed per iteration)
    #[arg(long, value_name = "FILE", conflicts_with = "input")]
    dataset: Option<PathBuf>,

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

/// Input source: either a single pre-parsed value or a dataset of raw lines.
enum InputSource {
    /// Single input parsed once — measures pure runtime overhead.
    Single(serde_json::Value),
    /// Dataset lines parsed per iteration — measures end-to-end including parse.
    Dataset {
        lines: Vec<String>,
        path: String,
        sha256: String,
    },
}

/// Parse a dataset line with a contextual error message.
fn parse_dataset_line(line: &str, index: usize) -> Result<serde_json::Value> {
    serde_json::from_str(line).with_context(|| {
        let preview: String = line.chars().take(80).collect();
        format!("dataset line {} failed to parse: {}", index + 1, preview)
    })
}

fn main() -> Result<()> {
    let args = Args::parse();

    // Require exactly one input mode
    if args.input.is_none() && args.dataset.is_none() {
        bail!("provide either --input FILE or --dataset FILE");
    }

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

    // Load input source
    let source = if let Some(ref path) = args.input {
        let input_str = std::fs::read_to_string(path)
            .with_context(|| format!("reading input file '{}'", path.display()))?;
        let json_input: serde_json::Value = serde_json::from_str(&input_str)
            .with_context(|| format!("parsing input JSON '{}'", path.display()))?;
        eprintln!("  input: {} (single, pre-parsed)", path.display());
        InputSource::Single(json_input)
    } else {
        let path = args.dataset.as_ref().unwrap();
        let raw = std::fs::read_to_string(path)
            .with_context(|| format!("reading dataset '{}'", path.display()))?;
        let sha256 = hex::encode(Sha256::digest(raw.as_bytes()));
        let lines: Vec<String> = raw.lines().filter(|l| !l.trim().is_empty()).map(String::from).collect();
        if lines.is_empty() {
            bail!("dataset '{}' contains no lines", path.display());
        }
        // Validate all lines parse before starting
        for (i, line) in lines.iter().enumerate() {
            parse_dataset_line(line, i)?;
        }
        eprintln!(
            "  dataset: {} ({} lines, sha256:{})",
            path.display(), lines.len(), &sha256[..16]
        );
        InputSource::Dataset {
            lines,
            path: path.display().to_string(),
            sha256,
        }
    };

    let opts = ExecuteOptions {
        trace_id: Some("bench-trace".to_string()),
        session_id: Some("bench-session".to_string()),
        verbose: false,
        all_terminals: false,
        ..Default::default()
    };

    let simulate_llm_ms = args.simulate_llm_ms;
    let pattern = args.llm_brick_pattern.as_str();
    let is_dataset = matches!(source, InputSource::Dataset { .. });

    // Warmup
    eprintln!("Warmup: {} iterations...", args.warmup);
    let mut tracer = NullTrace;
    for i in 0..args.warmup as usize {
        let json_input = match &source {
            InputSource::Single(v) => std::borrow::Cow::Borrowed(v),
            InputSource::Dataset { lines, .. } => {
                let di = i % lines.len();
                let v = parse_dataset_line(&lines[di], di)?;
                std::borrow::Cow::Owned(v)
            }
        };
        let mut hooks = ExecuteHooks::default();
        ctx.execute(&json_input, &mut tracer, &mut hooks, &opts)?;
    }

    // Timed runs
    eprintln!("Bench: {} iterations...", args.runs);
    let mut execute_us: Vec<u64> = Vec::with_capacity(args.runs as usize);
    let mut end_to_end_us: Vec<u64> = Vec::with_capacity(args.runs as usize);
    let mut total_llm_invokes: u64 = 0;
    let mut runs_with_llm: u64 = 0;
    let mut total_steps: u64 = 0;

    for i in 0..args.runs as usize {
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

        let e2e_start = Instant::now();
        let json_input = match &source {
            InputSource::Single(v) => std::borrow::Cow::Borrowed(v),
            InputSource::Dataset { lines, .. } => {
                let di = i % lines.len();
                let v = parse_dataset_line(&lines[di], di)?;
                std::borrow::Cow::Owned(v)
            }
        };
        let exec_start = Instant::now();
        let report = ctx.execute(&json_input, &mut tracer, &mut hooks, &opts)?;
        let exec_elapsed = exec_start.elapsed();
        let e2e_elapsed = e2e_start.elapsed();

        execute_us.push(exec_elapsed.as_micros() as u64);
        end_to_end_us.push(e2e_elapsed.as_micros() as u64);
        total_steps += report.total_steps;
        total_llm_invokes += run_llm_invokes;
        if run_llm_invokes > 0 {
            runs_with_llm += 1;
        }

        if report.terminals.is_empty() {
            bail!("bench run produced no terminal results");
        }
    }

    if execute_us.is_empty() {
        bail!("no runs completed");
    }

    execute_us.sort_unstable();
    end_to_end_us.sort_unstable();

    let count = execute_us.len() as u64;
    let exec_stats = compute_stats(&execute_us);
    let e2e_stats = compute_stats(&end_to_end_us);

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
    if is_dataset {
        eprintln!("  [end-to-end: parse + execute]");
        print_stats("  e2e", &e2e_stats);
        eprintln!("  [execute only]");
    }
    print_stats("  exec", &exec_stats);
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
    let mut results = serde_json::json!({
        "graph_id": ctx.graph_id(),
        "graph_version": ctx.graph_version(),
        "nodes": ctx.node_count(),
        "edges": ctx.edge_count(),
        "runs": count,
        "warmup": args.warmup,
        "mean_us": exec_stats.mean,
        "min_us": exec_stats.min,
        "max_us": exec_stats.max,
        "p50_us": exec_stats.p50,
        "p95_us": exec_stats.p95,
        "p99_us": exec_stats.p99,
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

    // Dataset-specific fields
    if let InputSource::Dataset { ref lines, ref path, ref sha256 } = source {
        let obj = results.as_object_mut().unwrap();
        obj.insert("mode".into(), serde_json::json!("dataset"));
        obj.insert("dataset_path".into(), serde_json::json!(path));
        obj.insert("dataset_size".into(), serde_json::json!(lines.len()));
        obj.insert("dataset_sha256".into(), serde_json::json!(sha256));
        obj.insert("e2e_mean_us".into(), serde_json::json!(e2e_stats.mean));
        obj.insert("e2e_min_us".into(), serde_json::json!(e2e_stats.min));
        obj.insert("e2e_max_us".into(), serde_json::json!(e2e_stats.max));
        obj.insert("e2e_p50_us".into(), serde_json::json!(e2e_stats.p50));
        obj.insert("e2e_p95_us".into(), serde_json::json!(e2e_stats.p95));
        obj.insert("e2e_p99_us".into(), serde_json::json!(e2e_stats.p99));
    } else {
        let obj = results.as_object_mut().unwrap();
        obj.insert("mode".into(), serde_json::json!("single"));
    }

    if let Some(path) = &args.output {
        std::fs::write(path, serde_json::to_string_pretty(&results)?)?;
        eprintln!("Results written to {}", path.display());
    } else {
        println!("{}", serde_json::to_string_pretty(&results)?);
    }

    Ok(())
}

struct Stats {
    mean: u64,
    min: u64,
    max: u64,
    p50: u64,
    p95: u64,
    p99: u64,
}

fn compute_stats(sorted: &[u64]) -> Stats {
    let count = sorted.len() as u64;
    let total: u64 = sorted.iter().sum();
    Stats {
        mean: total / count,
        min: sorted[0],
        max: sorted[sorted.len() - 1],
        p50: percentile(sorted, 50.0),
        p95: percentile(sorted, 95.0),
        p99: percentile(sorted, 99.0),
    }
}

fn percentile(sorted: &[u64], pct: f64) -> u64 {
    if sorted.is_empty() {
        return 0;
    }
    let idx = ((pct / 100.0) * (sorted.len() - 1) as f64).round() as usize;
    sorted[idx.min(sorted.len() - 1)]
}

fn print_stats(prefix: &str, s: &Stats) {
    eprintln!("{}  mean: {:>8} us  ({:.3} ms)", prefix, s.mean, s.mean as f64 / 1000.0);
    eprintln!("{}  min:  {:>8} us  ({:.3} ms)", prefix, s.min, s.min as f64 / 1000.0);
    eprintln!("{}  max:  {:>8} us  ({:.3} ms)", prefix, s.max, s.max as f64 / 1000.0);
    eprintln!("{}  p50:  {:>8} us  ({:.3} ms)", prefix, s.p50, s.p50 as f64 / 1000.0);
    eprintln!("{}  p95:  {:>8} us  ({:.3} ms)", prefix, s.p95, s.p95 as f64 / 1000.0);
    eprintln!("{}  p99:  {:>8} us  ({:.3} ms)", prefix, s.p99, s.p99 as f64 / 1000.0);
}
