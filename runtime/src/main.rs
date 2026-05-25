mod cli;

use std::path::PathBuf;

use anyhow::{Context, Result};
use clap::Parser;

use cli::{Cli, Command};
use ncp_runtime::{
    mapping, result,
    trace::{JsonlTraceWriter, TraceSink},
    ExecuteHooks, ExecuteOptions, RuntimeContext,
};

fn main() -> Result<()> {
    let cli = Cli::parse();
    match cli.command {
        Command::Run {
            graph,
            input,
            brick_dir,
            brick_map,
            trace,
            all_terminals,
            max_steps,
            max_queued,
            verbose,
        } => run_graph(
            graph,
            input,
            brick_dir,
            brick_map,
            trace,
            all_terminals,
            max_steps,
            max_queued,
            verbose,
        ),
    }
}

fn run_graph(
    graph: PathBuf,
    input: PathBuf,
    brick_dir: PathBuf,
    brick_map: Option<PathBuf>,
    trace_file: Option<PathBuf>,
    all_terminals: bool,
    max_steps: Option<u64>,
    max_queued: u64,
    verbose: bool,
) -> Result<()> {
    // Load and compile graph
    let ctx = RuntimeContext::load(&graph, &brick_dir, brick_map.as_deref())?;

    eprintln!(
        "Loaded graph '{}' with {} nodes, {} edges",
        ctx.graph_id(),
        ctx.node_count(),
        ctx.edge_count(),
    );
    for info in ctx.resolved_bricks() {
        eprintln!(
            "Resolved brick '{}' v{} ({} bytes, {})",
            info.brick_id, info.version, info.wasm_bytes, info.digest,
        );
    }
    eprintln!("Entry node: '{}'", ctx.entry_node_id());

    // Set up trace writer
    let mut tracer: Box<dyn TraceSink> = match &trace_file {
        Some(path) => Box::new(
            JsonlTraceWriter::file(path)
                .with_context(|| format!("opening trace file '{}'", path.display()))?,
        ),
        None => Box::new(JsonlTraceWriter::stderr()),
    };
    ctx.emit_runtime_info(tracer.as_mut());

    // Load input JSON
    let input_str = std::fs::read_to_string(&input)
        .with_context(|| format!("reading input file '{}'", input.display()))?;
    let json_input: serde_json::Value = serde_json::from_str(&input_str)
        .with_context(|| format!("parsing input JSON '{}'", input.display()))?;

    // Execute
    let opts = ExecuteOptions {
        max_steps,
        max_queued,
        all_terminals,
        verbose,
        ..Default::default()
    };
    let mut hooks = ExecuteHooks::default();
    let report = ctx.execute(&json_input, tracer.as_mut(), &mut hooks, &opts)?;

    // ── Final Output ───────────────────────────────────────────────
    if report.terminals.is_empty() {
        eprintln!("Warning: no terminal nodes reached (graph may have been budget-limited)");
        std::process::exit(1);
    }

    if all_terminals {
        let arr: Vec<serde_json::Value> = report
            .terminals
            .iter()
            .map(|t| {
                let mut obj = serde_json::json!({
                    "node_id": t.node_id,
                    "brick_id": t.brick_id,
                    "step": t.step,
                    "type": t.result.result_type(),
                });
                if let Some(output) = t.result.output() {
                    obj["output"] = mapping::cbor_to_json(output);
                }
                if let Some(error) = t.result.error() {
                    obj["error"] = serde_json::json!({
                        "error_class": error.error_class,
                        "message": error.message,
                    });
                }
                obj
            })
            .collect();
        println!("{}", serde_json::to_string_pretty(&arr)?);
    } else {
        let last = report.terminals.last().unwrap();
        match &last.result {
            result::BrickResult::Success { output }
            | result::BrickResult::LowConfidence { output, .. } => {
                let json = mapping::cbor_to_json(output);
                println!("{}", serde_json::to_string_pretty(&json)?);
            }
            result::BrickResult::Failure { error } => {
                let json = serde_json::json!({
                    "error": {
                        "error_class": error.error_class,
                        "message": error.message,
                    }
                });
                println!("{}", serde_json::to_string_pretty(&json)?);
                std::process::exit(1);
            }
        }
    }

    Ok(())
}
