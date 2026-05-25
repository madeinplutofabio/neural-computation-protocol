// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

//! End-to-end smoke tests for the reference runtime.
//!
//! These tests load each runnable example graph from `examples/graphs/`, execute
//! it against its committed `sample*.json` input, and verify the high-level
//! shape of the result (success / failure / path-taken). They run as part of
//! `cargo test -p ncp-runtime --all-targets` on every host (Linux + macOS +
//! Windows) — see `.github/workflows/rust.yml`.
//!
//! Paths are derived from `env!("CARGO_MANIFEST_DIR")` so the tests are
//! independent of the current working directory.
//!
//! ## Coverage
//! - `examples/graphs/echo-pipeline/` (single-node Success)
//! - `examples/graphs/echo-chain/` (two-node chain with on_success routing)
//! - `examples/graphs/support-routing-stubbed/` × {positive, escalation} —
//!   exercises the classifier → echo gate-and-escalate pattern
//! - `examples/graphs/trap-pipeline/` (WASM trap → Failure with COMPUTATION_ERROR)
//!
//! ## Excluded — and why
//! `examples/graphs/support-routing/` is intentionally NOT covered here:
//! its `sentiment-gate` and `llm-escalation` bricks under `examples/bricks/`
//! are fixture-only (manifest + schemas, no `.wasm` artifact). Loading the
//! graph would fail brick resolution. The graph exists for spec illustration,
//! not execution.

use std::path::{Path, PathBuf};

use ncp_runtime::result::BrickResult;
use ncp_runtime::trace::NullTrace;
use ncp_runtime::{ExecuteHooks, ExecuteOptions, ExecutionReport, RuntimeContext};

/// Absolute path to the repository root (one level up from `runtime/`).
fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("CARGO_MANIFEST_DIR has no parent — runtime/ must live under repo root")
        .to_path_buf()
}

/// Load a graph + input from repo-relative paths and execute it once with default options.
///
/// Panics with a contextual message if any step fails; smoke tests are expected
/// to load + execute cleanly.
fn run_graph(graph_rel: &str, input_rel: &str) -> ExecutionReport {
    let root = repo_root();
    let graph_path = root.join(graph_rel);
    let input_path = root.join(input_rel);

    let ctx = RuntimeContext::load(&graph_path, &root.join("examples/bricks"), None)
        .unwrap_or_else(|e| panic!("RuntimeContext::load({}) failed: {e:#}", graph_rel));

    let input_text = std::fs::read_to_string(&input_path)
        .unwrap_or_else(|e| panic!("read_to_string({}) failed: {e}", input_rel));
    let input: serde_json::Value = serde_json::from_str(&input_text)
        .unwrap_or_else(|e| panic!("parse JSON {} failed: {e}", input_rel));

    let mut tracer = NullTrace;
    let mut hooks = ExecuteHooks::default();
    let opts = ExecuteOptions::default();

    ctx.execute(&input, &mut tracer, &mut hooks, &opts)
        .unwrap_or_else(|e| panic!("execute({}) failed: {e:#}", graph_rel))
}

// ── Tests ───────────────────────────────────────────────────────────────────

#[test]
fn smoke_echo_pipeline() {
    let report = run_graph(
        "examples/graphs/echo-pipeline/graph.yaml",
        "examples/graphs/echo-pipeline/sample.json",
    );
    assert_eq!(
        report.counts.failure, 0,
        "echo-pipeline must not produce Failure"
    );
    assert!(
        report.counts.success >= 1,
        "echo-pipeline must produce ≥1 Success terminal; got success={}, low_confidence={}, failure={}",
        report.counts.success, report.counts.low_confidence, report.counts.failure
    );
    assert!(
        !report.terminals.is_empty(),
        "expected at least one terminal"
    );
}

#[test]
fn smoke_echo_chain() {
    let report = run_graph(
        "examples/graphs/echo-chain/graph.yaml",
        "examples/graphs/echo-chain/sample.json",
    );
    assert_eq!(
        report.counts.failure, 0,
        "echo-chain must not produce Failure"
    );
    assert!(
        report.counts.success >= 1,
        "echo-chain must produce ≥1 Success terminal; got success={}, low_confidence={}, failure={}",
        report.counts.success, report.counts.low_confidence, report.counts.failure
    );
}

#[test]
fn smoke_support_routing_stubbed_positive() {
    let report = run_graph(
        "examples/graphs/support-routing-stubbed/graph.yaml",
        "examples/graphs/support-routing-stubbed/sample-positive.json",
    );
    assert_eq!(
        report.counts.failure, 0,
        "positive path must not produce Failure"
    );
    assert!(report.counts.success >= 1);
    assert!(!report.terminals.is_empty());

    // Positive: classifier returns Success → terminal at the classifier
    // (no escalation edge taken). Order-independent: just verify some
    // terminal lives on the classifier brick.
    assert!(
        report
            .terminals
            .iter()
            .any(|t| t.brick_id.contains("classifier")),
        "positive path should terminate at the classifier brick; terminals={:?}",
        report
            .terminals
            .iter()
            .map(|t| &t.brick_id)
            .collect::<Vec<_>>()
    );
}

#[test]
fn smoke_support_routing_stubbed_escalation() {
    let report = run_graph(
        "examples/graphs/support-routing-stubbed/graph.yaml",
        "examples/graphs/support-routing-stubbed/sample.json",
    );
    assert_eq!(
        report.counts.failure, 0,
        "escalation path must not produce Failure (echo always succeeds)"
    );
    assert!(report.counts.success >= 1);
    assert!(!report.terminals.is_empty());

    // Escalation: classifier emits LowConfidence → on_error edge routes to
    // echo → terminal at echo. Order-independent.
    assert!(
        report.terminals.iter().any(|t| t.brick_id.contains("echo")),
        "escalation path should terminate at the echo brick; terminals={:?}",
        report
            .terminals
            .iter()
            .map(|t| &t.brick_id)
            .collect::<Vec<_>>()
    );
}

#[test]
fn smoke_trap_pipeline_fails_as_designed() {
    let report = run_graph(
        "examples/graphs/trap-pipeline/graph.yaml",
        "examples/graphs/trap-pipeline/sample.json",
    );
    assert_eq!(
        report.counts.success, 0,
        "trap-pipeline must not produce Success"
    );
    assert!(
        report.counts.failure >= 1,
        "trap-pipeline must produce ≥1 Failure terminal; got success={}, low_confidence={}, failure={}",
        report.counts.success, report.counts.low_confidence, report.counts.failure
    );
    assert!(!report.terminals.is_empty());

    // Trap → runtime catches → Failure { error }. We intentionally do NOT pin
    // `error_class` to a specific string (e.g. "COMPUTATION_ERROR"): trap
    // classification can drift across Wasmtime versions. The two stable
    // invariants are (a) some terminal IS a Failure, and (b) it carries a
    // non-empty error_class. We grab any Failure terminal — terminal ordering
    // is not a test invariant.
    let failure = report
        .terminals
        .iter()
        .find(|t| matches!(&t.result, BrickResult::Failure { .. }))
        .expect("expected at least one Failure terminal");

    match &failure.result {
        BrickResult::Failure { error } => assert!(
            !error.error_class.is_empty(),
            "Failure terminal must carry a non-empty error_class"
        ),
        _ => unreachable!(),
    }
}
