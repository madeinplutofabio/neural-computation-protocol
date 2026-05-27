//! Integration tests for the `ncp-mcp-server` binary (PR B scope).
//!
//! These tests drive the binary as a subprocess via Cargo's
//! `CARGO_BIN_EXE_ncp-mcp-server` env var (avoids hardcoded `target/debug/...`
//! paths that break on Windows `.exe` suffix or release profile).
//!
//! Coverage:
//! - One graph loads, summary printed, exit 0, stdout empty.
//! - Two distinct graphs load, both names printed, exit 0, stdout empty.
//! - Two colliding graphs fail at startup with clear error, stdout empty.
//! - `--trace-dir` pointing at an existing file (not a dir) errors at
//!   startup, stdout empty.
//! - `--trace-dir` pointing at a non-existent directory creates it,
//!   exit 0, stdout empty.
//!
//! Every test passes `--check` so the binary validates startup (loads
//! graphs, derives tool names, validates uniqueness, validates
//! `--trace-dir`) and exits BEFORE constructing the tokio runtime or
//! starting the MCP stdio service. This keeps the tests focused on the
//! startup-validation surface PR B added, without depending on the MCP
//! protocol implementation that lands in PR C.
//!
//! Every test also asserts stdout is EMPTY. In `--check` mode there is
//! no MCP traffic, so the binary writes only to stderr; this catches
//! stdout pollution from the load/validate code paths early. The full
//! process-level JSON-RPC stdout-discipline test lives in
//! `tests/mcp_protocol.rs` (PR C file 8) and exercises the running
//! server.
//!
//! All tests construct paths relative to `CARGO_MANIFEST_DIR` so they work
//! regardless of the test runner's CWD.

use std::path::PathBuf;
use std::process::{Command, Output};

/// Absolute path to the built `ncp-mcp-server` binary (provided by Cargo).
fn binary() -> &'static str {
    env!("CARGO_BIN_EXE_ncp-mcp-server")
}

/// Absolute path to the workspace root (two levels up from this crate).
fn workspace_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent() // crates/
        .and_then(|p| p.parent()) // workspace root
        .expect("CARGO_MANIFEST_DIR has at least two parents")
        .to_path_buf()
}

fn echo_pipeline_graph() -> PathBuf {
    workspace_root().join("examples/graphs/echo-pipeline/graph.yaml")
}

fn echo_chain_graph() -> PathBuf {
    workspace_root().join("examples/graphs/echo-chain/graph.yaml")
}

fn brick_dir() -> PathBuf {
    workspace_root().join("examples/bricks")
}

/// Returns a unique temp path for this test, deleting any prior version.
/// Tests should clean up at the end, but this guard handles stale state
/// from prior failed runs.
fn unique_temp_path(test_name: &str) -> PathBuf {
    let p = std::env::temp_dir().join(format!(
        "ncp-mcp-server-test-{}-{}",
        test_name,
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&p);
    let _ = std::fs::remove_file(&p);
    p
}

fn run_binary(args: &[&str]) -> Output {
    Command::new(binary())
        .args(args)
        .output()
        .expect("failed to spawn ncp-mcp-server binary")
}

fn assert_stdout_empty(output: &Output) {
    assert!(
        output.stdout.is_empty(),
        "stdout should be empty in PR B:\nstdout: {}\nstderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

fn assert_stderr_contains(output: &Output, needle: &str) {
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains(needle),
        "stderr did not contain `{needle}`:\nstdout: {}\nstderr: {}",
        String::from_utf8_lossy(&output.stdout),
        stderr
    );
}

// ── happy path: single graph ───────────────────────────────────────────

#[test]
fn one_graph_loads_and_prints_tool_name() {
    let output = run_binary(&[
        "--check",
        "--graph",
        echo_pipeline_graph().to_str().unwrap(),
        "--brick-dir",
        brick_dir().to_str().unwrap(),
    ]);

    assert_stdout_empty(&output);

    assert!(
        output.status.success(),
        "exit non-zero:\nstdout: {}\nstderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    assert_stderr_contains(&output, "loaded 1 graph(s) as MCP tools");
    assert_stderr_contains(&output, "org.ncp-examples.echo-pipeline");
}

// ── happy path: two distinct graphs ────────────────────────────────────

#[test]
fn two_distinct_graphs_load_both_names_printed() {
    let output = run_binary(&[
        "--check",
        "--graph",
        echo_pipeline_graph().to_str().unwrap(),
        "--graph",
        echo_chain_graph().to_str().unwrap(),
        "--brick-dir",
        brick_dir().to_str().unwrap(),
    ]);

    assert_stdout_empty(&output);

    assert!(
        output.status.success(),
        "exit non-zero:\nstdout: {}\nstderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );

    assert_stderr_contains(&output, "loaded 2 graph(s) as MCP tools");
    assert_stderr_contains(&output, "org.ncp-examples.echo-pipeline");
    assert_stderr_contains(&output, "org.ncp-examples.echo-chain");
}

// ── collision: same graph loaded twice ─────────────────────────────────

#[test]
fn two_colliding_graphs_fail_with_clear_error() {
    // Loading the same graph path twice produces the same derived tool name
    // → collision detected at startup.
    let path = echo_pipeline_graph();
    let path_str = path.to_str().unwrap();

    let output = run_binary(&[
        "--check",
        "--graph",
        path_str,
        "--graph",
        path_str,
        "--brick-dir",
        brick_dir().to_str().unwrap(),
    ]);

    assert_stdout_empty(&output);

    assert!(
        !output.status.success(),
        "expected exit non-zero, got success:\nstderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );

    assert_stderr_contains(&output, "tool name");
    assert_stderr_contains(&output, "must produce unique tool names");
}

// ── --trace-dir validation: existing file is rejected ──────────────────

#[test]
fn trace_dir_pointing_at_existing_file_errors() {
    // Create a regular file (not a dir) at the trace-dir location.
    let target = unique_temp_path("trace-dir-is-file");
    std::fs::write(&target, b"hello").expect("failed to create test file");

    let output = run_binary(&[
        "--check",
        "--graph",
        echo_pipeline_graph().to_str().unwrap(),
        "--brick-dir",
        brick_dir().to_str().unwrap(),
        "--trace-dir",
        target.to_str().unwrap(),
    ]);

    assert_stdout_empty(&output);

    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    let _ = std::fs::remove_file(&target); // cleanup

    assert!(
        !output.status.success(),
        "expected exit non-zero, got success:\nstderr: {stderr}"
    );
    assert!(
        stderr.contains("exists but is not a directory"),
        "stderr did not contain expected message:\nstderr: {stderr}"
    );
}

// ── --trace-dir validation: non-existent dir is created ────────────────

#[test]
fn trace_dir_pointing_at_non_existent_path_is_created() {
    let target = unique_temp_path("trace-dir-non-existent");
    assert!(!target.exists(), "test setup: target should not exist yet");

    let output = run_binary(&[
        "--check",
        "--graph",
        echo_pipeline_graph().to_str().unwrap(),
        "--brick-dir",
        brick_dir().to_str().unwrap(),
        "--trace-dir",
        target.to_str().unwrap(),
    ]);

    assert_stdout_empty(&output);

    let stderr = String::from_utf8_lossy(&output.stderr).to_string();
    let exists = target.exists();
    let is_dir = target.is_dir();
    let _ = std::fs::remove_dir_all(&target); // cleanup

    assert!(output.status.success(), "exit non-zero:\nstderr: {stderr}");
    assert!(exists, "trace-dir was not created");
    assert!(is_dir, "trace-dir exists but is not a directory");
}
