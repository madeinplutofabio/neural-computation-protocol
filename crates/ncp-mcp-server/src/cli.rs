//! CLI parsing, graph loading, and startup validation.
//!
//! This module is the entry-point logic that `src/main.rs` delegates to.
//! In PR C, it loads each `--graph` manifest via `RuntimeContext::load`,
//! derives MCP tool names via [`crate::naming`], validates uniqueness,
//! validates the `--trace-dir` (if set), and prints a startup summary to
//! stderr. Then:
//!
//! - If `--check` is set, exits `Ok(())` without constructing the tokio runtime.
//! - Otherwise, builds a multi-thread tokio runtime and runs the MCP server
//!   (see [`crate::server`]) until stdin closes (EOF).

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use anyhow::{anyhow, Context, Result};
use clap::Parser;
use ncp_runtime::RuntimeContext;

use crate::naming;
use crate::server::{self, StartupState, ToolEntry};

/// `ncp-mcp-server` — MCP adapter for NCP graphs.
#[derive(Parser, Debug)]
#[command(
    name = "ncp-mcp-server",
    version,
    about = "Stdio MCP adapter exposing NCP graphs as MCP tools"
)]
pub struct Cli {
    /// Path to an NCP graph manifest (repeat the flag for multiple graphs).
    #[arg(long = "graph", required = true, value_name = "PATH")]
    pub graphs: Vec<PathBuf>,

    /// Directory containing brick subdirectories.
    #[arg(
        long = "brick-dir",
        default_value = "examples/bricks",
        value_name = "DIR"
    )]
    pub brick_dir: PathBuf,

    /// Brick-map file; overrides --brick-dir for listed brick IDs.
    #[arg(long = "brick-map", value_name = "FILE")]
    pub brick_map: Option<PathBuf>,

    /// Directory for per-call trace files (`<dir>/<trace_id>.jsonl`).
    /// If absent, traces are dropped (NullTrace).
    #[arg(long = "trace-dir", value_name = "DIR")]
    pub trace_dir: Option<PathBuf>,

    /// Validate startup (load graphs, derive tool names, validate uniqueness
    /// and trace-dir), print the summary, then exit 0 WITHOUT starting the
    /// MCP server. Useful for config-validation in CI or before connecting
    /// to an MCP host.
    #[arg(long = "check")]
    pub check: bool,
}

/// Validates `--trace-dir` per docs/MCP_ADAPTER.md §12:
/// - Returns `Ok(None)` if not set.
/// - Creates the directory recursively if it doesn't exist.
/// - Fails if the path exists but is not a directory.
/// - Returns the canonicalized absolute path.
pub fn validate_trace_dir(trace_dir: Option<&Path>) -> Result<Option<PathBuf>> {
    let Some(dir) = trace_dir else {
        return Ok(None);
    };

    if dir.exists() {
        if !dir.is_dir() {
            return Err(anyhow!(
                "--trace-dir `{}` exists but is not a directory",
                dir.display()
            ));
        }
    } else {
        std::fs::create_dir_all(dir)
            .with_context(|| format!("failed to create --trace-dir `{}`", dir.display()))?;
    }

    let canonical = std::fs::canonicalize(dir)
        .with_context(|| format!("failed to canonicalize --trace-dir `{}`", dir.display()))?;
    Ok(Some(canonical))
}

/// Loads all graphs, derives tool names, validates uniqueness, and assembles
/// the `StartupState` that the MCP server will consume.
///
/// Per docs/MCP_ADAPTER.md §11, contexts are kept (wrapped in `Arc`) so they
/// can be shared across `tokio::task::spawn_blocking` invocations during
/// concurrent `tools/call` requests.
fn load_startup_state(cli: &Cli) -> Result<StartupState> {
    let trace_dir = validate_trace_dir(cli.trace_dir.as_deref())?;

    let mut staged: Vec<(String, ToolEntry)> = Vec::with_capacity(cli.graphs.len());
    for graph_path in &cli.graphs {
        let ctx = RuntimeContext::load(graph_path, &cli.brick_dir, cli.brick_map.as_deref())
            .with_context(|| format!("failed to load graph `{}`", graph_path.display()))?;

        let tool_name = naming::graph_id_to_tool_name(ctx.graph_id()).with_context(|| {
            format!(
                "failed to derive tool name from graph `{}`",
                graph_path.display()
            )
        })?;

        staged.push((
            tool_name,
            ToolEntry {
                graph_path: graph_path.clone(),
                graph_id: ctx.graph_id().to_string(),
                graph_version: ctx.graph_version().to_string(),
                context: Arc::new(ctx),
            },
        ));
    }

    // Validate uniqueness using (path, name) pairs derived from the staged
    // entries. Cloning at startup is cheap; loads dominate the cost.
    let pairs: Vec<(PathBuf, String)> = staged
        .iter()
        .map(|(name, entry)| (entry.graph_path.clone(), name.clone()))
        .collect();
    naming::validate_no_collisions(&pairs)?;

    // Move into the final HashMap (consumes staged).
    let tools: HashMap<String, ToolEntry> = staged.into_iter().collect();
    Ok(StartupState { tools, trace_dir })
}

/// Prints the loaded-tool summary line to stderr.
///
/// Stdout is reserved for JSON-RPC protocol traffic (per §7); summary goes to
/// stderr in both `--check` mode and server mode so MCP-host log capture sees
/// the same startup signal regardless. Tool names are sorted for deterministic
/// output (HashMap iteration order is otherwise non-deterministic).
fn print_summary(state: &StartupState) {
    let mut names: Vec<&str> = state.tools.keys().map(String::as_str).collect();
    names.sort();
    eprintln!(
        "loaded {} graph(s) as MCP tools: {}",
        names.len(),
        names.join(", ")
    );
}

/// PR C startup flow: parse CLI, load + validate, print summary; then either
/// exit (--check) or build tokio runtime and run the MCP server.
///
/// `--check` returns BEFORE tokio runtime construction (per docs/MCP_ADAPTER.md
/// §11 refinement) so:
/// - `--check` mode has zero async setup overhead.
/// - `--check` mode preserves the PR B invariant that stdout stays empty.
pub fn run() -> Result<()> {
    let cli = Cli::parse();
    let startup = load_startup_state(&cli)?;
    print_summary(&startup);

    if cli.check {
        return Ok(());
    }

    // No `.enable_all()` / `.enable_io()`: v0 is stdio-only and does not use
    // tokio timers or the reactor-backed I/O driver. `tokio::io::stdin/stdout`
    // works with the `io-std` feature; PR C keeps runtime drivers minimal.
    let rt = tokio::runtime::Builder::new_multi_thread()
        .build()
        .context("failed to build tokio runtime")?;

    rt.block_on(server::run(startup))
}
