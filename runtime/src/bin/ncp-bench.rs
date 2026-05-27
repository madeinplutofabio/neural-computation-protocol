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
//! Optional features:
//!   --simulate-llm-ms `<ms>`   Inject sleep after matching nodes (models LLM I/O).
//!   --concurrency N          Run N worker threads in parallel; report throughput.
//!   --cold-start             Measure load + compile time separately.
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
//!
//!   # Throughput with 4 worker threads
//!   cargo run --release --bin ncp-bench -- \
//!     examples/graphs/echo-pipeline/graph.yaml \
//!     --input examples/graphs/echo-pipeline/sample.json \
//!     --runs 10000 --warmup 500 --concurrency 4

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Instant;

use anyhow::{bail, Context, Result};
use clap::Parser;
use sha2::{Digest, Sha256};

use ncp_runtime::{trace::NullTrace, ExecuteHooks, ExecuteOptions, InvokeMetric, RuntimeContext};

fn _assert_send_sync<T: Send + Sync>() {}

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

    /// Number of timed iterations (total across all threads)
    #[arg(long, default_value = "1000")]
    runs: u64,

    /// Number of warmup iterations (not counted, per thread)
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

    /// Number of worker threads (default 1 = single-threaded latency mode)
    #[arg(long, default_value = "1")]
    concurrency: usize,

    /// Measure cold-start time (load + compile) separately
    #[arg(long)]
    cold_start: bool,
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

/// Per-thread collected results.
struct ThreadResults {
    execute_us: Vec<u64>,
    end_to_end_us: Vec<u64>,
    total_llm_invokes: u64,
    runs_with_llm: u64,
    total_steps: u64,
}

/// Parse a dataset line with a contextual error message.
fn parse_dataset_line(line: &str, index: usize) -> Result<serde_json::Value> {
    serde_json::from_str(line).with_context(|| {
        let preview: String = line.chars().take(80).collect();
        format!("dataset line {} failed to parse: {}", index + 1, preview)
    })
}

fn main() -> Result<()> {
    _assert_send_sync::<RuntimeContext>();

    let args = Args::parse();

    // Require exactly one input mode
    if args.input.is_none() && args.dataset.is_none() {
        bail!("provide either --input FILE or --dataset FILE");
    }

    if args.concurrency < 1 {
        bail!("--concurrency must be >= 1");
    }

    // Load and compile once; optionally time it for cold-start measurement
    let (ctx, cold_start_us) = if args.cold_start {
        let start = Instant::now();
        let ctx = RuntimeContext::load(&args.graph, &args.brick_dir, args.brick_map.as_deref())?;
        let us = start.elapsed().as_micros() as u64;
        eprintln!("Cold start: {} us ({:.3} ms)", us, us as f64 / 1000.0);
        (ctx, Some(us))
    } else {
        (
            RuntimeContext::load(&args.graph, &args.brick_dir, args.brick_map.as_deref())?,
            None,
        )
    };

    let ctx = Arc::new(ctx);

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
    if args.concurrency > 1 {
        eprintln!("  concurrency: {} threads", args.concurrency);
    }

    // Load input source
    let source = Arc::new(if let Some(ref path) = args.input {
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
        let lines: Vec<String> = raw
            .lines()
            .filter(|l| !l.trim().is_empty())
            .map(String::from)
            .collect();
        if lines.is_empty() {
            bail!("dataset '{}' contains no lines", path.display());
        }
        // Validate all lines parse before starting
        for (i, line) in lines.iter().enumerate() {
            parse_dataset_line(line, i)?;
        }
        eprintln!(
            "  dataset: {} ({} lines, sha256:{})",
            path.display(),
            lines.len(),
            &sha256[..16]
        );
        InputSource::Dataset {
            lines,
            path: path.display().to_string(),
            sha256,
        }
    });

    let simulate_llm_ms = args.simulate_llm_ms;
    let pattern: Arc<str> = Arc::from(args.llm_brick_pattern.as_str());
    let is_dataset = matches!(source.as_ref(), InputSource::Dataset { .. });
    let simulating = simulate_llm_ms.is_some();

    // Warmup (single-threaded)
    eprintln!("Warmup: {} iterations...", args.warmup);
    {
        let mut tracer = NullTrace;
        let opts = ExecuteOptions {
            trace_id: Some("bench-trace".to_string()),
            session_id: Some("bench-session".to_string()),
            verbose: false,
            all_terminals: false,
            ..Default::default()
        };
        for i in 0..args.warmup as usize {
            let json_input = match source.as_ref() {
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
    }

    // Distribute runs across threads
    let num_threads = args.concurrency;
    let total_runs = args.runs as usize;
    let base_per_thread = total_runs / num_threads;
    let remainder = total_runs % num_threads;

    eprintln!(
        "Bench: {} iterations across {} thread(s)...",
        total_runs, num_threads
    );

    let wall_start = Instant::now();

    let handles: Vec<_> = (0..num_threads)
        .map(|t| {
            let ctx = Arc::clone(&ctx);
            let source = Arc::clone(&source);
            let pattern = Arc::clone(&pattern);
            let runs = base_per_thread + if t < remainder { 1 } else { 0 };
            // Offset so each thread works on different dataset indices
            let index_offset = t * base_per_thread + t.min(remainder);

            std::thread::spawn(move || -> Result<ThreadResults> {
                let mut tracer = NullTrace;
                let opts = ExecuteOptions {
                    trace_id: Some("bench-trace".to_string()),
                    session_id: Some("bench-session".to_string()),
                    verbose: false,
                    all_terminals: false,
                    ..Default::default()
                };

                let mut execute_us: Vec<u64> = Vec::with_capacity(runs);
                let mut end_to_end_us: Vec<u64> = Vec::with_capacity(runs);
                let mut total_llm_invokes: u64 = 0;
                let mut runs_with_llm: u64 = 0;
                let mut total_steps: u64 = 0;

                for i in 0..runs {
                    let mut run_llm_invokes: u64 = 0;

                    let mut on_invoke = |metric: InvokeMetric| {
                        if !simulating {
                            return;
                        }
                        if metric.brick_id.contains(&*pattern) {
                            run_llm_invokes += 1;
                            std::thread::sleep(std::time::Duration::from_millis(
                                simulate_llm_ms.unwrap(),
                            ));
                        }
                    };

                    let mut hooks = ExecuteHooks {
                        on_invoke: Some(&mut on_invoke),
                    };

                    let e2e_start = Instant::now();
                    let json_input = match source.as_ref() {
                        InputSource::Single(v) => std::borrow::Cow::Borrowed(v),
                        InputSource::Dataset { lines, .. } => {
                            let di = (index_offset + i) % lines.len();
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

                Ok(ThreadResults {
                    execute_us,
                    end_to_end_us,
                    total_llm_invokes,
                    runs_with_llm,
                    total_steps,
                })
            })
        })
        .collect();

    // Collect results from all threads
    let mut all_execute_us: Vec<u64> = Vec::with_capacity(total_runs);
    let mut all_e2e_us: Vec<u64> = Vec::with_capacity(total_runs);
    let mut total_llm_invokes: u64 = 0;
    let mut runs_with_llm: u64 = 0;
    let mut total_steps: u64 = 0;

    for handle in handles {
        let tr = handle
            .join()
            .map_err(|_| anyhow::anyhow!("worker thread panicked"))??;
        all_execute_us.extend(tr.execute_us);
        all_e2e_us.extend(tr.end_to_end_us);
        total_llm_invokes += tr.total_llm_invokes;
        runs_with_llm += tr.runs_with_llm;
        total_steps += tr.total_steps;
    }

    let wall_elapsed = wall_start.elapsed();

    if all_execute_us.is_empty() {
        bail!("no runs completed");
    }

    all_execute_us.sort_unstable();
    all_e2e_us.sort_unstable();

    let count = all_execute_us.len() as u64;
    let exec_stats = compute_stats(&all_execute_us);
    let e2e_stats = compute_stats(&all_e2e_us);

    let mean_steps_per_run = total_steps as f64 / count as f64;
    let wall_secs = wall_elapsed.as_secs_f64();
    let throughput_rps = count as f64 / wall_secs;

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
    if simulating {
        let llm_invokes_per_run = total_llm_invokes as f64 / count as f64;
        let p_llm_requests = runs_with_llm as f64 / count as f64;
        let k_llm = if runs_with_llm > 0 {
            total_llm_invokes as f64 / runs_with_llm as f64
        } else {
            0.0
        };
        eprintln!("  llm_invokes:    {}", total_llm_invokes);
        eprintln!("  runs_with_llm:  {}", runs_with_llm);
        eprintln!("  p_llm_requests: {:.4}", p_llm_requests);
        eprintln!("  k_llm:          {:.4}", k_llm);
        eprintln!("  llm_invokes/run:{:.4}", llm_invokes_per_run);
        eprintln!("  simulate_llm:   {} ms", simulate_llm_ms.unwrap());
    }
    if num_threads > 1 {
        eprintln!("  wall_time:      {:.3} ms", wall_secs * 1000.0);
        eprintln!("  throughput:     {:.0} req/s", throughput_rps);
    }
    if let Some(us) = cold_start_us {
        eprintln!("  cold_start:     {} us ({:.3} ms)", us, us as f64 / 1000.0);
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
        "concurrency": num_threads,
        "wall_ms": (wall_secs * 1000.0).round() as u64,
        "throughput_rps": (throughput_rps * 100.0).round() / 100.0,
        "runtime_version": ncp_runtime::RUNTIME_VERSION,
        "wasmtime_version": ncp_runtime::WASMTIME_MAJOR,
        "timestamp_utc": ncp_runtime::now_rfc3339(),
    });

    let obj = results.as_object_mut().unwrap();

    // LLM metrics: only when simulating
    if simulating {
        let p_llm_requests = runs_with_llm as f64 / count as f64;
        let k_llm = if runs_with_llm > 0 {
            total_llm_invokes as f64 / runs_with_llm as f64
        } else {
            0.0
        };
        obj.insert("simulate_llm_ms".into(), serde_json::json!(simulate_llm_ms));
        obj.insert(
            "llm_brick_pattern".into(),
            serde_json::json!(pattern.as_ref()),
        );
        obj.insert("llm_invokes".into(), serde_json::json!(total_llm_invokes));
        obj.insert(
            "llm_invokes_per_run".into(),
            serde_json::json!(total_llm_invokes as f64 / count as f64),
        );
        obj.insert("runs_with_llm".into(), serde_json::json!(runs_with_llm));
        obj.insert("p_llm_requests".into(), serde_json::json!(p_llm_requests));
        obj.insert("k_llm".into(), serde_json::json!(k_llm));
    }

    // Cold-start
    if let Some(us) = cold_start_us {
        obj.insert("cold_start_us".into(), serde_json::json!(us));
    }

    // Dataset-specific fields
    if let InputSource::Dataset {
        lines,
        path,
        sha256,
    } = source.as_ref()
    {
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
    eprintln!(
        "{}  mean: {:>8} us  ({:.3} ms)",
        prefix,
        s.mean,
        s.mean as f64 / 1000.0
    );
    eprintln!(
        "{}  min:  {:>8} us  ({:.3} ms)",
        prefix,
        s.min,
        s.min as f64 / 1000.0
    );
    eprintln!(
        "{}  max:  {:>8} us  ({:.3} ms)",
        prefix,
        s.max,
        s.max as f64 / 1000.0
    );
    eprintln!(
        "{}  p50:  {:>8} us  ({:.3} ms)",
        prefix,
        s.p50,
        s.p50 as f64 / 1000.0
    );
    eprintln!(
        "{}  p95:  {:>8} us  ({:.3} ms)",
        prefix,
        s.p95,
        s.p95 as f64 / 1000.0
    );
    eprintln!(
        "{}  p99:  {:>8} us  ({:.3} ms)",
        prefix,
        s.p99,
        s.p99 as f64 / 1000.0
    );
}
