// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

//! End-to-end graph integration tests for the lead-qualification example
//! graph (issue #29).
//!
//! Loads the production graph via `RuntimeContext::load`, executes 11
//! fixtures through the real NCP runtime (scheduler, edge routing,
//! envelope encoding, Wasmtime invocation, result decoding, failure
//! isolation), and asserts bit-exact business outputs plus invocation
//! order via `ExecuteHooks`.
//!
//! Determinism is pinned by fixing `trace_id` + `session_id` in
//! `ExecuteOptions`; comparisons are on `BrickResult` business fields,
//! not on execution-metadata-tainted whole reports.
//!
//! Corrupted-digest rejection is tested in a tempdir copy of both brick
//! directories; the production artifact and manifest are never touched.

use std::cell::RefCell;
use std::path::{Path, PathBuf};

use ncp_runtime::manifest::load_graph;
use ncp_runtime::result::{BrickResult, CborValue};
use ncp_runtime::trace::NullTrace;
use ncp_runtime::{
    ExecuteHooks, ExecuteOptions, ExecutionReport, InvokeMetric, RuntimeContext, TerminalResult,
};
use serde_json::Value;

// ---- Path helpers ------------------------------------------------------

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("CARGO_MANIFEST_DIR has no parent -- runtime/ must live under repo root")
        .to_path_buf()
}
fn graph_path() -> PathBuf {
    repo_root().join("examples/graphs/lead-qualification/graph.yaml")
}
fn brick_dir() -> PathBuf {
    repo_root().join("examples/bricks")
}
fn fixture_path(name: &str) -> PathBuf {
    repo_root()
        .join("examples/graphs/lead-qualification/samples")
        .join(name)
}

// ---- Fixture loading + execution ---------------------------------------

fn load_fixture(name: &str) -> Value {
    let path = fixture_path(name);
    let s =
        std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {}", path.display(), e));
    serde_json::from_str(&s).unwrap_or_else(|e| panic!("parse {}: {}", path.display(), e))
}

fn load_runtime() -> RuntimeContext {
    RuntimeContext::load(&graph_path(), &brick_dir(), None)
        .expect("RuntimeContext::load must succeed")
}

/// Run a fixture through the full runtime with fixed trace/session IDs.
/// Returns the execution report + the ordered list of per-node invocation
/// metrics captured via `on_invoke`.
fn run_fixture(name: &str) -> (ExecutionReport, Vec<InvokeMetric>) {
    let ctx = load_runtime();
    run_input(&ctx, &load_fixture(name))
}

fn run_input(ctx: &RuntimeContext, input: &Value) -> (ExecutionReport, Vec<InvokeMetric>) {
    let invokes: RefCell<Vec<InvokeMetric>> = RefCell::new(Vec::new());
    let mut on_invoke = |m: InvokeMetric| invokes.borrow_mut().push(m);
    let mut hooks = ExecuteHooks {
        on_invoke: Some(&mut on_invoke),
    };
    let mut tracer = NullTrace;
    let opts = ExecuteOptions {
        trace_id: Some("test-trace".to_string()),
        session_id: Some("test-session".to_string()),
        ..Default::default()
    };
    let report = ctx
        .execute(input, &mut tracer, &mut hooks, &opts)
        .expect("execute must succeed");
    (report, invokes.into_inner())
}

// ---- CborValue field extraction helpers --------------------------------

fn as_success_output(terminal: &TerminalResult) -> &CborValue {
    match &terminal.result {
        BrickResult::Success { output } => output,
        other => panic!(
            "expected Success at node {}, got {:?}",
            terminal.node_id,
            other.result_type()
        ),
    }
}

fn as_failure_error(terminal: &TerminalResult) -> &ncp_runtime::result::ErrorObject {
    match &terminal.result {
        BrickResult::Failure { error } => error,
        other => panic!(
            "expected Failure at node {}, got {:?}",
            terminal.node_id,
            other.result_type()
        ),
    }
}

fn get_field<'a>(v: &'a CborValue, key: &str) -> &'a CborValue {
    let m = match v {
        CborValue::Map(m) => m,
        other => panic!("expected Map, got {:?}", other),
    };
    m.iter()
        .find_map(|(k, val)| match k {
            CborValue::Text(t) if t == key => Some(val),
            _ => None,
        })
        .unwrap_or_else(|| panic!("key {:?} not present in output", key))
}

fn get_text(v: &CborValue, key: &str) -> String {
    match get_field(v, key) {
        CborValue::Text(t) => t.clone(),
        other => panic!("{}: expected Text, got {:?}", key, other),
    }
}

fn get_bool_opt(v: &CborValue, key: &str) -> Option<bool> {
    match get_field(v, key) {
        CborValue::Null => None,
        CborValue::Bool(b) => Some(*b),
        other => panic!("{}: expected Bool or Null, got {:?}", key, other),
    }
}

fn get_score_bits(v: &CborValue, key: &str) -> Option<u64> {
    match get_field(v, key) {
        CborValue::Null => None,
        CborValue::Float(f) => Some(f.to_bits()),
        other => panic!("{}: expected Float or Null, got {:?}", key, other),
    }
}

fn get_text_opt(v: &CborValue, key: &str) -> Option<String> {
    match get_field(v, key) {
        CborValue::Null => None,
        CborValue::Text(t) => Some(t.clone()),
        other => panic!("{}: expected Text or Null, got {:?}", key, other),
    }
}

fn get_reasons(v: &CborValue, key: &str) -> Vec<String> {
    match get_field(v, key) {
        CborValue::Array(a) => a
            .iter()
            .map(|r| match r {
                CborValue::Text(t) => t.clone(),
                other => panic!("{} entry: expected Text, got {:?}", key, other),
            })
            .collect(),
        other => panic!("{}: expected Array, got {:?}", key, other),
    }
}

// ---- Mandatory per-fixture assertions ----------------------------------

/// For every valid-input fixture: 2 invocations in order lookup -> qualify,
/// terminal is qualify, both Success.
fn assert_success_sequence<'a>(
    invokes: &[InvokeMetric],
    report: &'a ExecutionReport,
) -> &'a TerminalResult {
    assert_eq!(
        invokes.len(),
        2,
        "valid-input fixture must invoke exactly 2 bricks, got {}",
        invokes.len()
    );
    assert_eq!(
        invokes[0].node_id, "lookup",
        "first invocation must be lookup"
    );
    assert_eq!(
        invokes[0].brick_id,
        "org.ncp-examples.company-profile-lookup"
    );
    assert_eq!(invokes[0].result_type, "Success");
    assert_eq!(
        invokes[1].node_id, "qualify",
        "second invocation must be qualify"
    );
    assert_eq!(invokes[1].brick_id, "org.ncp-examples.icp-qualifier");
    assert_eq!(invokes[1].result_type, "Success");
    assert_eq!(report.terminals.len(), 1, "exactly 1 terminal");
    let term = &report.terminals[0];
    assert_eq!(term.node_id, "qualify", "terminal must be qualify");
    term
}

/// For failure fixtures: exactly 1 invocation at lookup, qualify not
/// invoked, terminal is Failure(INVALID_INPUT) at lookup.
fn assert_failure_sequence<'a>(
    invokes: &[InvokeMetric],
    report: &'a ExecutionReport,
) -> &'a TerminalResult {
    assert_eq!(
        invokes.len(),
        1,
        "failure fixture must invoke exactly 1 brick (qualify NOT invoked), got {}",
        invokes.len()
    );
    assert_eq!(
        invokes[0].node_id, "lookup",
        "only invocation must be lookup"
    );
    assert_eq!(
        invokes[0].brick_id,
        "org.ncp-examples.company-profile-lookup"
    );
    assert_eq!(invokes[0].result_type, "Failure");
    assert_eq!(report.terminals.len(), 1);
    let term = &report.terminals[0];
    assert_eq!(term.node_id, "lookup", "terminal must be lookup");
    let err = as_failure_error(term);
    assert_eq!(
        err.error_class, "INVALID_INPUT",
        "terminal failure must be INVALID_INPUT"
    );
    term
}

// ======================================================================
// 1-7, 11: 8 valid-input fixtures
// ======================================================================

#[test]
fn fixture_01_qualified_3_of_3() {
    let (report, invokes) = run_fixture("qualified_3_of_3.json");
    let term = assert_success_sequence(&invokes, &report);
    let out = as_success_output(term);
    assert_eq!(get_text(out, "next_action"), "qualified");
    assert_eq!(get_bool_opt(out, "icp_match"), Some(true));
    assert_eq!(
        get_score_bits(out, "score"),
        Some((3.0f64 / 3.0f64).to_bits())
    );
    assert_eq!(
        get_text_opt(out, "company_name").as_deref(),
        Some("Example SaaS")
    );
    assert_eq!(get_text_opt(out, "industry").as_deref(), Some("B2B SaaS"));
    assert_eq!(get_reasons(out, "reasons").len(), 3);
}

#[test]
fn fixture_02_qualified_2_of_3() {
    let (report, invokes) = run_fixture("qualified_2_of_3.json");
    let term = assert_success_sequence(&invokes, &report);
    let out = as_success_output(term);
    assert_eq!(get_text(out, "next_action"), "qualified");
    assert_eq!(get_bool_opt(out, "icp_match"), Some(true));
    assert_eq!(
        get_score_bits(out, "score"),
        Some((2.0f64 / 3.0f64).to_bits())
    );
}

#[test]
fn fixture_03_not_qualified_1_of_3_path_a_dependency() {
    // Factory Example (industry=manufacturing, segment=enterprise,
    // audience=b2b) vs target "b2b saas smb creators".
    // Path A: b2b standalone token inside "b2b saas" IS an audience
    // descriptor -> audience_match=true. Segment and industry mismatch.
    // matched=1 -> not_qualified, score=1/3.
    let (report, invokes) = run_fixture("not_qualified_1_of_3.json");
    let term = assert_success_sequence(&invokes, &report);
    let out = as_success_output(term);
    assert_eq!(get_text(out, "next_action"), "not_qualified");
    assert_eq!(get_bool_opt(out, "icp_match"), Some(false));
    assert_eq!(
        get_score_bits(out, "score"),
        Some((1.0f64 / 3.0f64).to_bits())
    );
    assert_eq!(
        get_text_opt(out, "company_name").as_deref(),
        Some("Factory Example")
    );
}

#[test]
fn fixture_04_not_qualified_0_of_3_no_fallback() {
    // Shop Example (retail/SMB/B2C) vs target "b2b saas enterprise creators".
    // All 3 dims interpretable, zero match. Must emit not_qualified
    // NOT needs_enrichment.
    let (report, invokes) = run_fixture("not_qualified_0_of_3.json");
    let term = assert_success_sequence(&invokes, &report);
    let out = as_success_output(term);
    assert_eq!(get_text(out, "next_action"), "not_qualified");
    assert_eq!(get_bool_opt(out, "icp_match"), Some(false));
    assert_eq!(
        get_score_bits(out, "score"),
        Some((0.0f64 / 3.0f64).to_bits())
    );
    assert_eq!(
        get_text_opt(out, "company_name").as_deref(),
        Some("Shop Example")
    );
}

#[test]
fn fixture_05_needs_enrichment_unknown_profile() {
    let (report, invokes) = run_fixture("needs_enrichment_unknown.json");
    let term = assert_success_sequence(&invokes, &report);
    let out = as_success_output(term);
    assert_eq!(get_text(out, "next_action"), "needs_enrichment");
    assert_eq!(get_bool_opt(out, "icp_match"), None);
    assert_eq!(get_score_bits(out, "score"), None);
    assert_eq!(get_text_opt(out, "company_name"), None);
    assert_eq!(get_text_opt(out, "industry"), None);
    let reasons = get_reasons(out, "reasons");
    assert_eq!(reasons.len(), 1);
    assert_eq!(reasons[0], "Company domain not in catalog");
}

#[test]
fn fixture_06_needs_enrichment_unsupported_icp_unicode() {
    // Unicode target_icp (rocket emoji + 3 unrecognized ASCII words).
    // All 3 dims uninterpretable; known profile => facts preserved.
    let (report, invokes) = run_fixture("needs_enrichment_unsupported_icp.json");
    let term = assert_success_sequence(&invokes, &report);
    let out = as_success_output(term);
    assert_eq!(get_text(out, "next_action"), "needs_enrichment");
    assert_eq!(get_bool_opt(out, "icp_match"), None);
    assert_eq!(get_score_bits(out, "score"), None);
    // Known profile => facts preserved on needs_enrichment-via-ICP.
    assert_eq!(
        get_text_opt(out, "company_name").as_deref(),
        Some("Example SaaS")
    );
    assert_eq!(get_text_opt(out, "industry").as_deref(), Some("B2B SaaS"));
    let reasons = get_reasons(out, "reasons");
    assert_eq!(reasons.len(), 1);
    assert_eq!(
        reasons[0],
        "Target ICP contains no supported terms in industry, segment, audience"
    );
}

#[test]
fn fixture_07_needs_enrichment_partial_icp_no_partial_score() {
    // Target "manufacturing enterprise" -> industry+segment interpretable,
    // audience uninterpretable. Even though 2 of 3 dims could be scored
    // (both would be mismatches here), needs_enrichment must fire and
    // score must be null -- no partial score leaks through.
    let (report, invokes) = run_fixture("needs_enrichment_partial_icp.json");
    let term = assert_success_sequence(&invokes, &report);
    let out = as_success_output(term);
    assert_eq!(get_text(out, "next_action"), "needs_enrichment");
    assert_eq!(get_bool_opt(out, "icp_match"), None);
    assert_eq!(
        get_score_bits(out, "score"),
        None,
        "score MUST be null when a dim is uninterpretable (no partial scoring)"
    );
    let reasons = get_reasons(out, "reasons");
    assert_eq!(reasons.len(), 1);
    assert_eq!(
        reasons[0],
        "Target ICP contains no supported terms in audience"
    );
    assert_eq!(
        get_text_opt(out, "company_name").as_deref(),
        Some("Example SaaS")
    );
}

#[test]
fn fixture_11_qualified_with_notes_identical_business_output_as_fixture_01() {
    // Notes propagation: fixture 1 and fixture 11 have identical URLs
    // and target_icp but fixture 11 has notes="Introduced by the sales
    // team.". The qualifier's 6-field output must be byte-identical
    // between the two fixtures: notes propagates through the edge, is
    // present in brick 2's input, is ignored by scoring, and does not
    // appear in brick 2's output.
    let (report_1, invokes_1) = run_fixture("qualified_3_of_3.json");
    let (report_11, invokes_11) = run_fixture("qualified_3_of_3_with_notes.json");

    // Both must follow lookup -> qualify Success/Success.
    assert_success_sequence(&invokes_1, &report_1);
    assert_success_sequence(&invokes_11, &report_11);

    // Terminal business outputs byte-identical.
    let out_1 = as_success_output(&report_1.terminals[0]);
    let out_11 = as_success_output(&report_11.terminals[0]);
    assert_cbor_eq(out_1, out_11);
}

// ======================================================================
// 8-10: failure fixtures (qualify must NOT be invoked)
// ======================================================================

#[test]
fn fixture_08_failure_missing_url_blocks_qualifier() {
    let (report, invokes) = run_fixture("failure_missing_url.json");
    assert_failure_sequence(&invokes, &report);
}

#[test]
fn fixture_09_failure_bad_type_blocks_qualifier() {
    let (report, invokes) = run_fixture("failure_bad_type.json");
    assert_failure_sequence(&invokes, &report);
}

#[test]
fn fixture_10_failure_malformed_url_blocks_qualifier() {
    let (report, invokes) = run_fixture("failure_malformed_url.json");
    assert_failure_sequence(&invokes, &report);
}

// ======================================================================
// Graph-level structural assertions
// ======================================================================

#[test]
fn graph_declares_exactly_seven_mapping_pairs_in_canonical_order() {
    // Static graph.yaml inspection via load_graph (no execution). Verifies
    // the edge mapping declaration itself matches the agreed 7-field
    // contract -- not the envelope the qualifier actually receives, which
    // the hooks API does not expose.
    let graph = load_graph(&graph_path()).expect("load graph");
    assert_eq!(graph.edges.len(), 1, "exactly one edge in the graph");
    let edge = &graph.edges[0];
    assert_eq!(edge.edge_id, "lookup_to_qualify");
    assert_eq!(edge.source_node, "lookup");
    assert_eq!(edge.target_node, "qualify");
    let expected: &[(&str, &str)] = &[
        ("output.company_name", "input.company_name"),
        ("output.industry", "input.industry"),
        ("output.segment", "input.segment"),
        ("output.audience", "input.audience"),
        ("output.profile_found", "input.profile_found"),
        ("output.target_icp", "input.target_icp"),
        ("output.notes", "input.notes"),
    ];
    assert_eq!(
        edge.mapping.len(),
        expected.len(),
        "exactly 7 mapping pairs"
    );
    for (i, (from, to)) in expected.iter().enumerate() {
        assert_eq!(&edge.mapping[i].from, from, "mapping[{}].from", i);
        assert_eq!(&edge.mapping[i].to, to, "mapping[{}].to", i);
    }
    // company_url_normalized intentionally NOT mapped (acceptance item 2).
    for fm in &edge.mapping {
        assert_ne!(
            fm.from, "output.company_url_normalized",
            "company_url_normalized must NOT be mapped to the qualifier"
        );
    }
    // on_success present (required for runtime dispatch).
    assert!(edge.on_success.is_some(), "edge must have on_success");
}

// ======================================================================
// Determinism (fixed trace_id + session_id -> identical business output)
// ======================================================================

#[test]
fn determinism_across_all_three_outcome_branches() {
    let ctx = load_runtime();
    let samples = [
        "qualified_3_of_3.json",
        "qualified_2_of_3.json",
        "not_qualified_0_of_3.json",
        "not_qualified_1_of_3.json",
        "needs_enrichment_unknown.json",
        "needs_enrichment_unsupported_icp.json",
        "needs_enrichment_partial_icp.json",
        "qualified_3_of_3_with_notes.json",
    ];
    for name in &samples {
        let input = load_fixture(name);
        let (r1, _) = run_input(&ctx, &input);
        let (r2, _) = run_input(&ctx, &input);
        assert_eq!(r1.terminals.len(), 1);
        assert_eq!(r2.terminals.len(), 1);
        let out1 = as_success_output(&r1.terminals[0]);
        let out2 = as_success_output(&r2.terminals[0]);
        assert_cbor_eq_msg(out1, out2, name);
    }
}

#[test]
fn determinism_failure_fixtures_business_errors_identical() {
    let ctx = load_runtime();
    for name in [
        "failure_missing_url.json",
        "failure_bad_type.json",
        "failure_malformed_url.json",
    ] {
        let input = load_fixture(name);
        let (r1, _) = run_input(&ctx, &input);
        let (r2, _) = run_input(&ctx, &input);
        let e1 = as_failure_error(&r1.terminals[0]);
        let e2 = as_failure_error(&r2.terminals[0]);
        assert_eq!(e1.error_class, e2.error_class, "fixture {}", name);
        assert_eq!(e1.message, e2.message, "fixture {}", name);
    }
}

// ======================================================================
// Corrupted-digest rejection (temp dir copy, production never touched)
// ======================================================================

#[test]
fn corrupted_brick2_digest_causes_graph_load_to_fail_with_digest_mismatch() {
    use std::fs;
    // Create a tempdir and copy both brick directories into it.
    let temp = tempfile::tempdir().expect("tempdir");
    let temp_brick_dir = temp.path();
    copy_brick_dir(
        &repo_root().join("examples/bricks/company-profile-lookup"),
        &temp_brick_dir.join("company-profile-lookup"),
    );
    copy_brick_dir(
        &repo_root().join("examples/bricks/icp-qualifier"),
        &temp_brick_dir.join("icp-qualifier"),
    );
    // Mutate brick 2 manifest's digest: flip one hex nibble.
    let manifest_path = temp_brick_dir.join("icp-qualifier/manifest.yaml");
    let content = fs::read_to_string(&manifest_path).expect("read brick2 manifest");
    // Known prefix of the production digest; flip 'f' -> '0'.
    let needle = "sha256:f4c50217";
    let replacement = "sha256:04c50217";
    assert!(
        content.contains(needle),
        "expected digest prefix not found in manifest"
    );
    let corrupted = content.replace(needle, replacement);
    assert_ne!(content, corrupted, "corruption must change the file");
    fs::write(&manifest_path, &corrupted).expect("write corrupted manifest");

    // Attempt to load the graph pointing at the temp dir. Must fail with
    // a digest mismatch at the brick 2 resolver step, not with a missing
    // directory or malformed manifest.
    let err = match RuntimeContext::load(&graph_path(), temp_brick_dir, None) {
        Ok(_) => panic!("load must fail with corrupted brick 2 digest"),
        Err(e) => e,
    };
    let msg = format!("{err:#}");
    assert!(
        msg.contains("digest mismatch"),
        "error must include 'digest mismatch', got: {}",
        msg
    );
    assert!(
        msg.contains("org.ncp-examples.icp-qualifier"),
        "error must identify brick 2 as the culprit, got: {}",
        msg
    );
}

// ======================================================================
// Edge-mapping mutation test (production graph untouched)
// ======================================================================

#[test]
fn removing_notes_edge_mapping_causes_qualifier_to_reject_incomplete_input() {
    // Prove the Brick-to-Brick interface contract is enforced at runtime,
    // not merely documented in the YAML. Mutation: delete the single
    // mapping pair `output.notes -> input.notes` in a tempdir copy of
    // the graph. Expected cascade:
    //   step 0: lookup executes successfully (envelope includes notes).
    //   step 1: qualify receives a 6-field input (missing notes field).
    //           Parser's Pass 1 structural scan returns MissingNotes.
    //           Terminal: Failure(INVALID_INPUT) at qualify.
    //   Exactly 2 invocations occurred -- qualify DID run (unlike
    //   failure fixtures 8-10, where qualify is never dispatched because
    //   lookup emits Failure(INVALID_INPUT)).
    use std::fs;
    let temp = tempfile::tempdir().expect("tempdir");
    let orig_graph = fs::read_to_string(graph_path())
        .expect("read graph.yaml")
        .replace("\r\n", "\n");
    // Delete the two YAML lines for the notes mapping. Fixed indentation
    // in the locked graph.yaml makes this a safe string replacement.
    // Line endings are normalized to LF above so this works on Windows CI,
    // where git may check out YAML files with CRLF endings.
    let notes_mapping_block = "      - from: output.notes\n        to: input.notes\n";
    assert!(
        orig_graph.contains(notes_mapping_block),
        "expected notes mapping block not found in production graph.yaml"
    );
    let mutated = orig_graph.replace(notes_mapping_block, "");
    assert_ne!(orig_graph, mutated, "mutation must change the YAML");

    let mutated_graph_path = temp.path().join("graph.yaml");
    fs::write(&mutated_graph_path, &mutated).expect("write mutated graph");

    // Load via RuntimeContext using the mutated graph + production bricks.
    let ctx = RuntimeContext::load(&mutated_graph_path, &brick_dir(), None)
        .expect("load mutated graph (6-field mapping) must succeed structurally");
    // Verify the mutation really dropped notes from the mapping.
    let mutated_manifest = load_graph(&mutated_graph_path).expect("load mutated graph manifest");
    assert_eq!(
        mutated_manifest.edges[0].mapping.len(),
        6,
        "mutated graph must have 6 mapping pairs, not 7"
    );
    assert!(
        !mutated_manifest.edges[0]
            .mapping
            .iter()
            .any(|fm| fm.from == "output.notes"),
        "notes mapping must be absent in mutated graph"
    );

    // Execute a valid fixture (one WITH notes to maximise signal).
    let input = load_fixture("qualified_3_of_3_with_notes.json");
    let (report, invokes) = run_input(&ctx, &input);

    // Exactly 2 invocations: lookup succeeded, qualify was dispatched
    // and ran (unlike pre-lookup failure cases).
    assert_eq!(invokes.len(), 2, "both bricks must execute");
    assert_eq!(invokes[0].node_id, "lookup");
    assert_eq!(invokes[0].result_type, "Success");
    assert_eq!(invokes[1].node_id, "qualify");
    assert_eq!(
        invokes[1].result_type, "Failure",
        "qualify must reject incomplete input"
    );

    // Terminal is Failure(INVALID_INPUT) at qualify.
    assert_eq!(report.terminals.len(), 1);
    let term = &report.terminals[0];
    assert_eq!(term.node_id, "qualify");
    let err = as_failure_error(term);
    assert_eq!(err.error_class, "INVALID_INPUT");
    // No qualification decision emitted -- Pass 1's MissingNotes variant
    // produces an INVALID_INPUT message naming the missing field.
    assert!(
        err.message.contains("notes"),
        "error message must identify missing notes field, got: {}",
        err.message
    );
}

fn copy_brick_dir(src: &Path, dst: &Path) {
    use std::fs;
    fs::create_dir_all(dst).expect("mkdir dst");
    for entry in fs::read_dir(src).expect("read_dir src") {
        let entry = entry.expect("entry");
        let ft = entry.file_type().expect("file_type");
        let src_path = entry.path();
        let dst_path = dst.join(entry.file_name());
        if ft.is_dir() {
            copy_brick_dir(&src_path, &dst_path);
        } else if ft.is_file() {
            fs::copy(&src_path, &dst_path).expect("copy file");
        }
    }
}

// ---- Deep structural CBOR equality (no execution-metadata leak) --------

fn assert_cbor_eq(a: &CborValue, b: &CborValue) {
    assert_cbor_eq_msg(a, b, "")
}

fn assert_cbor_eq_msg(a: &CborValue, b: &CborValue, ctx: &str) {
    if !cbor_eq(a, b) {
        let ctx_tag = if ctx.is_empty() {
            String::new()
        } else {
            format!(" ({})", ctx)
        };
        panic!("CBOR values differ{}:\n  a: {:?}\n  b: {:?}", ctx_tag, a, b);
    }
}

fn cbor_eq(a: &CborValue, b: &CborValue) -> bool {
    match (a, b) {
        (CborValue::Null, CborValue::Null) => true,
        (CborValue::Bool(x), CborValue::Bool(y)) => x == y,
        (CborValue::Integer(x), CborValue::Integer(y)) => x == y,
        (CborValue::Float(x), CborValue::Float(y)) => x.to_bits() == y.to_bits(),
        (CborValue::Text(x), CborValue::Text(y)) => x == y,
        (CborValue::Bytes(x), CborValue::Bytes(y)) => x == y,
        (CborValue::Array(x), CborValue::Array(y)) => {
            x.len() == y.len() && x.iter().zip(y.iter()).all(|(a, b)| cbor_eq(a, b))
        }
        (CborValue::Map(x), CborValue::Map(y)) => {
            x.len() == y.len()
                && x.iter()
                    .zip(y.iter())
                    .all(|((ka, va), (kb, vb))| cbor_eq(ka, kb) && cbor_eq(va, vb))
        }
        _ => false,
    }
}
