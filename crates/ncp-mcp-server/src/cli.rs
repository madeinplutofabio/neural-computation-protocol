//! CLI parsing, graph loading, and startup validation.
//!
//! This module is the entry-point logic that `src/main.rs` delegates to.
//! In PR B, it loads each `--graph` manifest via `RuntimeContext::load`,
//! derives MCP tool names via [`crate::naming`], validates uniqueness,
//! validates the `--trace-dir` (if set), and prints a startup summary to
//! stderr. **No MCP protocol traffic** — that arrives in PR C.

use std::path::{Path, PathBuf};

use anyhow::{anyhow, Context, Result};
use clap::Parser;
use ncp_runtime::RuntimeContext;

use crate::naming;

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

    /// Path to a YAML/JSON brick-map file (overrides --brick-dir for listed brick IDs).
    #[arg(long = "brick-map", value_name = "FILE")]
    pub brick_map: Option<PathBuf>,

    /// Directory for per-call trace files (`<dir>/<trace_id>.jsonl`).
    /// If absent, traces are dropped (NullTrace).
    #[arg(long = "trace-dir", value_name = "DIR")]
    pub trace_dir: Option<PathBuf>,
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

/// PR B startup flow: parse CLI, validate trace-dir, load each graph,
/// derive tool names, validate uniqueness, print summary, exit 0.
///
/// PR C extends this to wire up the MCP server after the summary line.
pub fn run() -> Result<()> {
    let cli = Cli::parse();

    // 1. Validate + canonicalize --trace-dir (per §12).
    //    The canonicalized path is unused in PR B (no MCP traffic = no trace
    //    writes). PR C will use it to construct per-call trace files.
    let _trace_dir = validate_trace_dir(cli.trace_dir.as_deref())?;

    // 2. Load each graph via RuntimeContext::load() — validates the graph
    //    enough to construct a RuntimeContext (manifest parses, bricks resolve).
    //    Collect (path, derived tool name) pairs.
    //    PR B drops the loaded contexts after the summary; PR C will keep
    //    them in a HashMap<ToolName, Arc<RuntimeContext>> for tools/call.
    let mut pairs: Vec<(PathBuf, String)> = Vec::with_capacity(cli.graphs.len());
    for graph_path in &cli.graphs {
        let ctx = RuntimeContext::load(graph_path, &cli.brick_dir, cli.brick_map.as_deref())
            .with_context(|| format!("failed to load graph `{}`", graph_path.display()))?;

        let tool_name = naming::graph_id_to_tool_name(ctx.graph_id()).with_context(|| {
            format!(
                "failed to derive tool name from graph `{}`",
                graph_path.display()
            )
        })?;

        pairs.push((graph_path.clone(), tool_name));
    }

    // 3. Validate no two graphs derive the same tool name.
    naming::validate_no_collisions(&pairs)?;

    // 4. Print loaded-tool summary to stderr.
    //    Stdout is reserved for JSON-RPC protocol traffic (per §7); even in
    //    PR B where no protocol is wired up, we follow the discipline.
    let names: Vec<&str> = pairs.iter().map(|(_, n)| n.as_str()).collect();
    eprintln!(
        "loaded {} graph(s) as MCP tools: {}",
        names.len(),
        names.join(", ")
    );

    // 5. PR B exits cleanly here. PR C replaces this point with the MCP
    //    server loop (initialize / tools/list / tools/call over stdio).
    Ok(())
}
