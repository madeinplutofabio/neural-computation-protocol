use std::path::PathBuf;

use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(name = "ncp", version, about = "NCP reference runtime")]
pub struct Cli {
    #[command(subcommand)]
    pub command: Command,
}

#[derive(Subcommand)]
pub enum Command {
    /// Execute a graph
    Run {
        /// Path to the graph manifest (YAML)
        graph: PathBuf,

        /// Path to the JSON input file
        #[arg(long, value_name = "FILE")]
        input: PathBuf,

        /// Directory containing brick subdirectories
        #[arg(long, default_value = "examples/bricks", value_name = "DIR")]
        brick_dir: PathBuf,

        /// Path to a YAML/JSON brick-map file (overrides brick-dir for listed brick IDs)
        #[arg(long, value_name = "FILE")]
        brick_map: Option<PathBuf>,

        /// Write trace to a file instead of stderr
        #[arg(long, value_name = "FILE")]
        trace: Option<PathBuf>,

        /// Output all terminal results as a JSON array
        #[arg(long)]
        all_terminals: bool,

        /// Maximum number of invocation steps (default: unlimited)
        #[arg(long)]
        max_steps: Option<u64>,

        /// Maximum queued tasks before aborting (default: 10000)
        #[arg(long, default_value = "10000")]
        max_queued: u64,

        /// Print per-step diagnostics to stderr
        #[arg(long)]
        verbose: bool,
    },
}
