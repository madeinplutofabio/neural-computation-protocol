//! Binary entrypoint for `ncp-mcp-server`.
//!
//! Thin wrapper that delegates to `ncp_mcp_server::cli::run`. All
//! application logic lives in the library; this file exists to satisfy
//! Cargo's `[[bin]]` target.

fn main() -> anyhow::Result<()> {
    ncp_mcp_server::cli::run()
}
