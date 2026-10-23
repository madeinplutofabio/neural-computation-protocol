// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

//! Model B ABI integration test for ncp-company-profile-lookup brick.
//!
//! Loads the compiled WASM artifact, invokes it through the full Model B
//! ABI (alloc -> write -> invoke -> read -> free) via ncp_runtime's
//! CompiledBrick, and decodes the result through ncp_runtime::result::
//! decode_result. This verifies brick 1 is runtime-compatible end-to-end --
//! a stronger guarantee than the brick's own 101 native unit tests, which
//! exercise only host-side helper functions.
//!
//! The envelope is built via ncp_runtime::envelope::build_envelope with
//! ROOT_TRIGGER, exercising the real production envelope shape (4 fields:
//! carry_state, ctx, graph_refs, input), not a parser-friendly minimal
//! {input: ...} shape. This fulfills the previously deferred runtime-
//! envelope compatibility check.

use std::path::PathBuf;

use ncp_runtime::engine::CompiledBrick;
use ncp_runtime::envelope::{build_envelope, ROOT_TRIGGER};
use ncp_runtime::resolver::resolve_brick;
use ncp_runtime::result::{decode_result, BrickResult, CborValue};
use serde_json::json;

/// Path to the committed WASM artifact.
fn wasm_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("CARGO_MANIFEST_DIR has no parent -- runtime/ must live under repo root")
        .join("examples/bricks/company-profile-lookup/company-profile-lookup.wasm")
}

/// Load WASM bytes + construct CompiledBrick.
fn load_brick() -> CompiledBrick {
    let bytes = std::fs::read(wasm_path())
        .unwrap_or_else(|e| panic!("read {}: {}", wasm_path().display(), e));
    CompiledBrick::new(&bytes).expect("compile WASM module")
}

/// Build the full 4-field production envelope with the given application input.
fn test_envelope(company_url: &str, target_icp: &str, notes: Option<&str>) -> Vec<u8> {
    let mut input = json!({
        "company_url": company_url,
        "target_icp": target_icp,
    });
    if let Some(n) = notes {
        input["notes"] = json!(n);
    }
    build_envelope(
        &input,
        "lead-qualification-test",
        "test-version",
        "lookup",
        "test-trace",
        "test-session",
        0,
        &ROOT_TRIGGER,
    )
    .expect("build runtime envelope")
}

/// Walk a CborValue::Map and find a field by key.
fn get_map_field<'a>(value: &'a CborValue, key: &str) -> &'a CborValue {
    let map = match value {
        CborValue::Map(m) => m,
        other => panic!("expected CborValue::Map, got {:?}", other),
    };
    map.iter()
        .find_map(|(k, v)| match k {
            CborValue::Text(t) if t == key => Some(v),
            _ => None,
        })
        .unwrap_or_else(|| panic!("key {:?} not present in map", key))
}

// ── Tests ───────────────────────────────────────────────────────────────────

#[test]
fn abi_success_known_profile_example_com() {
    let brick = load_brick();

    // --- Raw / wrapped equivalence: both forms must produce byte-identical envelope.
    let raw_json = json!({
        "company_url": "https://example.com",
        "target_icp": "b2b saas smb creators",
    });
    let wrapped_json = json!({
        "input": {
            "company_url": "https://example.com",
            "target_icp": "b2b saas smb creators",
        }
    });
    let raw_env = build_envelope(
        &raw_json,
        "lead-qualification-test",
        "test-version",
        "lookup",
        "test-trace",
        "test-session",
        0,
        &ROOT_TRIGGER,
    )
    .expect("raw envelope");
    let wrapped_env = build_envelope(
        &wrapped_json,
        "lead-qualification-test",
        "test-version",
        "lookup",
        "test-trace",
        "test-session",
        0,
        &ROOT_TRIGGER,
    )
    .expect("wrapped envelope");
    assert_eq!(
        raw_env, wrapped_env,
        "raw and wrapped application input must produce identical envelope bytes"
    );

    // --- Invoke the brick with the real envelope.
    let envelope = raw_env;
    let result_bytes = brick
        .invoke(
            &envelope, 2,     /* max_mem_mb */
            65536, /* max_output_bytes */
        )
        .expect("brick invoke");

    // --- Determinism: a second invocation on byte-identical envelope must produce
    //     byte-identical result bytes. A fresh Wasmtime store is instantiated each
    //     time, so this probes result-encoding determinism across separate executions.
    let result_bytes_2 = brick.invoke(&envelope, 2, 65536).expect("brick invoke #2");
    assert_eq!(
        result_bytes, result_bytes_2,
        "repeated WASM invocation on identical envelope must produce identical result bytes"
    );

    // --- Decode and verify.
    let decoded = decode_result(&result_bytes).expect("decode result");
    let output = match decoded {
        BrickResult::Success { output } => output,
        other => panic!("expected Success, got {:?}", other.result_type()),
    };

    // Exact 8-field map contract.
    let fields = match &output {
        CborValue::Map(m) => m,
        other => panic!("Success output must be CborValue::Map, got {:?}", other),
    };
    assert_eq!(fields.len(), 8, "Success output must have exactly 8 fields");

    let expected_keys = [
        "audience",
        "company_name",
        "company_url_normalized",
        "industry",
        "notes",
        "profile_found",
        "segment",
        "target_icp",
    ];
    let actual_keys: Vec<String> = fields
        .iter()
        .map(|(k, _)| match k {
            CborValue::Text(t) => t.clone(),
            other => panic!("map key must be Text, got {:?}", other),
        })
        .collect();
    for expected in &expected_keys {
        assert!(
            actual_keys.iter().any(|a| a == expected),
            "expected key {} missing from output; actual keys: {:?}",
            expected,
            actual_keys,
        );
    }
    assert_eq!(
        actual_keys.len(),
        expected_keys.len(),
        "no extra keys allowed"
    );

    // Field values.
    match get_map_field(&output, "profile_found") {
        CborValue::Bool(true) => {}
        other => panic!("profile_found: expected Bool(true), got {:?}", other),
    }
    match get_map_field(&output, "industry") {
        CborValue::Text(t) if t == "B2B SaaS" => {}
        other => panic!("industry: {:?}", other),
    }
    match get_map_field(&output, "company_name") {
        CborValue::Text(t) if t == "Example SaaS" => {}
        other => panic!("company_name: {:?}", other),
    }
    match get_map_field(&output, "segment") {
        CborValue::Text(t) if t == "SMB" => {}
        other => panic!("segment: {:?}", other),
    }
    match get_map_field(&output, "audience") {
        CborValue::Text(t) if t == "creators" => {}
        other => panic!("audience: {:?}", other),
    }
    match get_map_field(&output, "company_url_normalized") {
        CborValue::Text(t) if t == "example.com" => {}
        other => panic!("company_url_normalized: {:?}", other),
    }
    match get_map_field(&output, "target_icp") {
        CborValue::Text(t) if t == "b2b saas smb creators" => {}
        other => panic!("target_icp passthrough: {:?}", other),
    }
    match get_map_field(&output, "notes") {
        CborValue::Null => {}
        other => panic!("notes: expected Null (not supplied), got {:?}", other),
    }
}

#[test]
fn abi_success_unknown_profile_yields_nulls_and_profile_found_false() {
    let brick = load_brick();
    let envelope = test_envelope("https://unknown.example.net", "b2b saas", None);
    let result_bytes = brick.invoke(&envelope, 2, 65536).expect("brick invoke");
    let decoded = decode_result(&result_bytes).expect("decode result");

    let output = match decoded {
        BrickResult::Success { output } => output,
        other => panic!("expected Success, got {:?}", other.result_type()),
    };

    match get_map_field(&output, "profile_found") {
        CborValue::Bool(false) => {}
        other => panic!("profile_found: expected Bool(false), got {:?}", other),
    }
    for field in ["company_name", "industry", "segment", "audience"] {
        match get_map_field(&output, field) {
            CborValue::Null => {}
            other => panic!("{}: expected Null, got {:?}", field, other),
        }
    }
    match get_map_field(&output, "company_url_normalized") {
        CborValue::Text(t) if t == "unknown.example.net" => {}
        other => panic!("company_url_normalized: {:?}", other),
    }
}

#[test]
fn abi_failure_invalid_input_malformed_envelope() {
    let brick = load_brick();

    // Pass a string where a map is expected. The runtime envelope encoder
    // serializes this as a CBOR text string under envelope.input. The brick
    // reports InputNotMap -> Failure(INVALID_INPUT).
    let input = json!("not a map");
    let envelope = build_envelope(
        &input,
        "lead-qualification-test",
        "test-version",
        "lookup",
        "test-trace",
        "test-session",
        0,
        &ROOT_TRIGGER,
    )
    .expect("build envelope with scalar input");

    let result_bytes = brick.invoke(&envelope, 2, 65536).expect("brick invoke");
    let decoded = decode_result(&result_bytes).expect("decode result");

    match decoded {
        BrickResult::Failure { error } => {
            assert_eq!(
                error.error_class, "INVALID_INPUT",
                "error_class must be INVALID_INPUT for malformed input"
            );
            assert!(
                !error.message.is_empty(),
                "message must be non-empty for diagnostic value"
            );
        }
        other => panic!("expected Failure, got {:?}", other.result_type()),
    }
}

/// Resolver acceptance check for brick 1's manifest.
///
/// Exercises `ncp_runtime::resolver::resolve_brick` on the on-disk
/// `examples/bricks/company-profile-lookup/` directory. The manifest omits
/// `artifact.path`, so the resolver must fall through to branch 2 of
/// `select_wasm` ("exactly one `.wasm` file in directory") and pick
/// `company-profile-lookup.wasm` automatically. `resolve_brick` also runs
/// `verify_artifact`, which re-hashes the WASM bytes and compares the
/// digest and size against the manifest -- a mismatch returns Err.
#[test]
fn resolver_selects_wasm_without_artifact_path_and_verifies_digest_and_size() {
    let brick_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("CARGO_MANIFEST_DIR has no parent -- runtime/ must live under repo root")
        .join("examples/bricks");

    let resolved = resolve_brick(
        "org.ncp-examples.company-profile-lookup",
        "^0.1.0",
        &brick_dir,
        &None,
    )
    .expect("resolve_brick must succeed");

    // Resolver auto-selected the expected WASM (branch 2: single .wasm in dir).
    let selected = resolved
        .wasm_path
        .file_name()
        .expect("wasm_path has a filename")
        .to_string_lossy()
        .into_owned();
    assert_eq!(
        selected, "company-profile-lookup.wasm",
        "resolver must auto-select the single .wasm file when artifact.path is absent"
    );

    // Manifest round-trip.
    assert_eq!(
        resolved.manifest.brick_id,
        "org.ncp-examples.company-profile-lookup"
    );
    assert_eq!(resolved.manifest.version, "0.1.0");
    assert_eq!(resolved.manifest.artifact.format, "wasm");
    assert_eq!(resolved.manifest.artifact.entrypoint, "invoke");
    assert!(
        resolved.manifest.artifact.path.is_none(),
        "artifact.path must be absent in brick 1's manifest"
    );
    assert_eq!(resolved.manifest.artifact.size_bytes, 30056);
    assert_eq!(
        resolved.manifest.artifact.digest,
        "sha256:ecc0b4e83061aae3ba6243aa3b7a248297af1d738b0154c6f752a52e10803d35"
    );

    // verify_artifact already compared size + sha256 against the declared values;
    // reaching this line proves both matched. Mirror the size check explicitly.
    assert_eq!(resolved.wasm_bytes.len(), 30056);
}

#[test]
fn abi_notes_passthrough_through_wasm() {
    let brick = load_brick();
    let envelope = test_envelope(
        "https://example.com",
        "b2b saas smb creators",
        Some("Introduced by the sales team."),
    );
    let result_bytes = brick.invoke(&envelope, 2, 65536).expect("brick invoke");
    let decoded = decode_result(&result_bytes).expect("decode result");

    let output = match decoded {
        BrickResult::Success { output } => output,
        other => panic!("expected Success, got {:?}", other.result_type()),
    };
    match get_map_field(&output, "notes") {
        CborValue::Text(t) if t == "Introduced by the sales team." => {}
        other => panic!("notes passthrough through WASM failed: {:?}", other),
    }
}
