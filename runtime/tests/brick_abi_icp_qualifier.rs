// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

//! Model B ABI integration test for ncp-icp-qualifier brick.
//!
//! Loads the compiled WASM artifact, invokes it through the full Model B
//! ABI (alloc -> write -> invoke -> read -> free) via ncp_runtime's
//! CompiledBrick, and decodes the result through ncp_runtime::result::
//! decode_result. This verifies brick 2 is runtime-compatible end-to-end
//! -- a stronger guarantee than the brick's own 118 native unit tests,
//! which exercise only host-side helper functions.
//!
//! Envelopes for normal cases are built via ncp_runtime::envelope::
//! build_envelope with ROOT_TRIGGER (the real production envelope shape:
//! 4 fields carry_state/ctx/graph_refs/input). Adversarial / malformed
//! cases that build_envelope cannot represent are hand-rolled.
//!
//! Resolver verification is deferred until the brick 2 manifest exists;
//! these tests load the WASM artifact directly.

use std::path::PathBuf;

use ncp_runtime::engine::CompiledBrick;
use ncp_runtime::envelope::{build_envelope, ROOT_TRIGGER};
use ncp_runtime::resolver::resolve_brick;
use ncp_runtime::result::{decode_result, BrickResult, CborValue, ErrorObject};
use serde_json::{json, Value};

// ---- Resource limits ----------------------------------------------------
//
// Module declares min=21 pages (~1.312 MiB) -- identical to brick 1.
// 2 MiB is the smallest valid budget. max_output_bytes 65536 matches
// the brick's declared MAX_ENVELOPE_BYTES.

const MAX_MEM_MB: u64 = 2;
const MAX_OUT_BYTES: u64 = 65_536;

// ---- Fixture helpers ----------------------------------------------------

fn wasm_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("CARGO_MANIFEST_DIR has no parent -- runtime/ must live under repo root")
        .join("examples/bricks/icp-qualifier/icp-qualifier.wasm")
}

fn load_brick() -> CompiledBrick {
    let bytes = std::fs::read(wasm_path())
        .unwrap_or_else(|e| panic!("read {}: {}", wasm_path().display(), e));
    CompiledBrick::new(&bytes).expect("compile WASM module")
}

/// Build a known-profile application input (all 7 fields).
fn known_app_input(
    company: &str,
    industry: &str,
    segment: &str,
    audience: &str,
    target_icp: &str,
) -> Value {
    json!({
        "audience": audience,
        "company_name": company,
        "industry": industry,
        "notes": Value::Null,
        "profile_found": true,
        "segment": segment,
        "target_icp": target_icp,
    })
}

/// Build an unknown-profile application input (profile_found=false, four facts null).
fn unknown_app_input(target_icp: &str) -> Value {
    json!({
        "audience": Value::Null,
        "company_name": Value::Null,
        "industry": Value::Null,
        "notes": Value::Null,
        "profile_found": false,
        "segment": Value::Null,
        "target_icp": target_icp,
    })
}

fn test_envelope(input: &Value) -> Vec<u8> {
    build_envelope(
        input,
        "lead-qualification-test",
        "test-version",
        "qualify",
        "test-trace",
        "test-session",
        0,
        &ROOT_TRIGGER,
    )
    .expect("build runtime envelope")
}

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
        .unwrap_or_else(|| panic!("key {:?} not present in output map", key))
}

fn as_success(result: BrickResult) -> CborValue {
    match result {
        BrickResult::Success { output } => output,
        other => panic!("expected Success, got {:?}", other.result_type()),
    }
}

fn as_failure(result: BrickResult) -> ErrorObject {
    match result {
        BrickResult::Failure { error } => error,
        other => panic!("expected Failure, got {:?}", other.result_type()),
    }
}

fn invoke_and_decode(brick: &CompiledBrick, envelope: &[u8]) -> BrickResult {
    let result_bytes = brick
        .invoke(envelope, MAX_MEM_MB, MAX_OUT_BYTES)
        .expect("brick.invoke must not trap");
    decode_result(&result_bytes).expect("decode_result must succeed")
}

// ---- Tests --------------------------------------------------------------

#[test]
fn abi_qualified_full_match_score_one() {
    let brick = load_brick();
    let input = known_app_input(
        "Example SaaS",
        "B2B SaaS",
        "SMB",
        "creators",
        "b2b saas smb creators",
    );
    let envelope = test_envelope(&input);
    let output = as_success(invoke_and_decode(&brick, &envelope));

    // next_action
    match get_map_field(&output, "next_action") {
        CborValue::Text(t) if t == "qualified" => {}
        other => panic!("next_action: {:?}", other),
    }
    // icp_match
    match get_map_field(&output, "icp_match") {
        CborValue::Bool(true) => {}
        other => panic!("icp_match: {:?}", other),
    }
    // score: exactly 1.0 (bit-exact)
    match get_map_field(&output, "score") {
        CborValue::Float(f) => assert_eq!(f.to_bits(), (3.0f64 / 3.0f64).to_bits()),
        other => panic!("score: {:?}", other),
    }
    // reasons length and ordering
    let reasons = match get_map_field(&output, "reasons") {
        CborValue::Array(a) => a,
        other => panic!("reasons: {:?}", other),
    };
    assert_eq!(reasons.len(), 3);
    match &reasons[0] {
        CborValue::Text(t) => assert!(t.starts_with("Industry ")),
        other => panic!("reasons[0]: {:?}", other),
    }
    match &reasons[1] {
        CborValue::Text(t) => assert!(t.starts_with("Segment ")),
        other => panic!("reasons[1]: {:?}", other),
    }
    match &reasons[2] {
        CborValue::Text(t) => assert!(t.starts_with("Audience ")),
        other => panic!("reasons[2]: {:?}", other),
    }
    // company_name / industry preserved
    match get_map_field(&output, "company_name") {
        CborValue::Text(t) if t == "Example SaaS" => {}
        other => panic!("company_name: {:?}", other),
    }
    match get_map_field(&output, "industry") {
        CborValue::Text(t) if t == "B2B SaaS" => {}
        other => panic!("industry: {:?}", other),
    }
}

#[test]
fn abi_qualified_two_of_three_score_two_thirds() {
    let brick = load_brick();
    // Example SaaS + "b2b saas smb b2b":
    //   industry match (b2b saas), segment match (smb),
    //   audience mismatch (profile=creators, target has only b2b).
    //   matched = 2 => score = 2/3 => qualified.
    let input = known_app_input(
        "Example SaaS",
        "B2B SaaS",
        "SMB",
        "creators",
        "b2b saas smb b2b",
    );
    let output = as_success(invoke_and_decode(&brick, &test_envelope(&input)));
    match get_map_field(&output, "next_action") {
        CborValue::Text(t) if t == "qualified" => {}
        other => panic!("next_action: {:?}", other),
    }
    match get_map_field(&output, "icp_match") {
        CborValue::Bool(true) => {}
        other => panic!("icp_match: {:?}", other),
    }
    match get_map_field(&output, "score") {
        CborValue::Float(f) => assert_eq!(f.to_bits(), (2.0f64 / 3.0f64).to_bits()),
        other => panic!("score: {:?}", other),
    }
}

#[test]
fn abi_not_qualified_zero_match_is_not_needs_enrichment() {
    // THE "no fallback" case: known profile, ALL dims interpretable,
    // zero matches. Expected: not_qualified with score=0.0, NOT
    // needs_enrichment.
    let brick = load_brick();
    let input = known_app_input(
        "Example SaaS",
        "B2B SaaS",
        "SMB",
        "creators",
        "manufacturing enterprise b2c",
    );
    let output = as_success(invoke_and_decode(&brick, &test_envelope(&input)));
    match get_map_field(&output, "next_action") {
        CborValue::Text(t) if t == "not_qualified" => {}
        other => panic!("next_action: {:?}", other),
    }
    match get_map_field(&output, "icp_match") {
        CborValue::Bool(false) => {}
        other => panic!("icp_match: {:?}", other),
    }
    match get_map_field(&output, "score") {
        CborValue::Float(f) => assert_eq!(f.to_bits(), (0.0f64 / 3.0f64).to_bits()),
        other => panic!("score: {:?}", other),
    }
    // Facts preserved on known profile.
    match get_map_field(&output, "company_name") {
        CborValue::Text(t) if t == "Example SaaS" => {}
        other => panic!("company_name: {:?}", other),
    }
}

#[test]
fn abi_not_qualified_one_match_score_one_third() {
    let brick = load_brick();
    // Example SaaS + "manufacturing smb b2b":
    //   industry mismatch, segment match (smb), audience mismatch.
    //   matched = 1 => score = 1/3 => not_qualified.
    let input = known_app_input(
        "Example SaaS",
        "B2B SaaS",
        "SMB",
        "creators",
        "manufacturing smb b2b",
    );
    let output = as_success(invoke_and_decode(&brick, &test_envelope(&input)));
    match get_map_field(&output, "next_action") {
        CborValue::Text(t) if t == "not_qualified" => {}
        other => panic!("next_action: {:?}", other),
    }
    match get_map_field(&output, "score") {
        CborValue::Float(f) => assert_eq!(f.to_bits(), (1.0f64 / 3.0f64).to_bits()),
        other => panic!("score: {:?}", other),
    }
}

#[test]
fn abi_needs_enrichment_unknown_profile_null_score_and_match() {
    let brick = load_brick();
    let input = unknown_app_input("b2b saas smb creators");
    let output = as_success(invoke_and_decode(&brick, &test_envelope(&input)));

    match get_map_field(&output, "next_action") {
        CborValue::Text(t) if t == "needs_enrichment" => {}
        other => panic!("next_action: {:?}", other),
    }
    match get_map_field(&output, "icp_match") {
        CborValue::Null => {}
        other => panic!(
            "icp_match must be null for needs_enrichment, got {:?}",
            other
        ),
    }
    match get_map_field(&output, "score") {
        CborValue::Null => {}
        other => panic!("score must be null for needs_enrichment, got {:?}", other),
    }
    // Unknown profile: company_name and industry are also null.
    match get_map_field(&output, "company_name") {
        CborValue::Null => {}
        other => panic!("company_name: {:?}", other),
    }
    match get_map_field(&output, "industry") {
        CborValue::Null => {}
        other => panic!("industry: {:?}", other),
    }
    // Single reason: "Company domain not in catalog".
    match get_map_field(&output, "reasons") {
        CborValue::Array(a) => {
            assert_eq!(a.len(), 1);
            match &a[0] {
                CborValue::Text(t) => {
                    assert_eq!(t, "Company domain not in catalog")
                }
                other => panic!("reasons[0]: {:?}", other),
            }
        }
        other => panic!("reasons: {:?}", other),
    }
}

#[test]
fn abi_needs_enrichment_icp_uninterpretable_preserves_company_facts() {
    let brick = load_brick();
    // Known profile but target_icp has no supported vocab terms.
    let input = known_app_input(
        "Example SaaS",
        "B2B SaaS",
        "SMB",
        "creators",
        "selling to teams of hobbyists",
    );
    let output = as_success(invoke_and_decode(&brick, &test_envelope(&input)));
    match get_map_field(&output, "next_action") {
        CborValue::Text(t) if t == "needs_enrichment" => {}
        other => panic!("next_action: {:?}", other),
    }
    match get_map_field(&output, "icp_match") {
        CborValue::Null => {}
        other => panic!("icp_match: {:?}", other),
    }
    match get_map_field(&output, "score") {
        CborValue::Null => {}
        other => panic!("score: {:?}", other),
    }
    // CRITICAL: company_name and industry preserved on known profile.
    match get_map_field(&output, "company_name") {
        CborValue::Text(t) if t == "Example SaaS" => {}
        other => panic!(
            "company_name must be preserved on needs_enrichment-via-ICP, got {:?}",
            other
        ),
    }
    match get_map_field(&output, "industry") {
        CborValue::Text(t) if t == "B2B SaaS" => {}
        other => panic!(
            "industry must be preserved on needs_enrichment-via-ICP, got {:?}",
            other
        ),
    }
}

#[test]
fn abi_failure_invalid_input_scalar_envelope_input() {
    // build_envelope with a scalar input (string) produces an envelope
    // whose envelope.input is not a Map. Parser rejects as InputNotMap
    // -> Failure(INVALID_INPUT). Must NEVER convert to needs_enrichment.
    let brick = load_brick();
    let input = json!("not a map");
    let envelope = test_envelope(&input);
    let error = as_failure(invoke_and_decode(&brick, &envelope));
    assert_eq!(error.error_class, "INVALID_INPUT");
    assert!(
        !error.message.is_empty(),
        "message must be non-empty for diagnostic value"
    );
}

#[test]
fn abi_failure_invalid_input_pass3_violation_never_becomes_needs_enrichment() {
    // profile_found=false with a non-null company_name violates the
    // input schema's conditional invariant. The parser's Pass 3 returns
    // FactsMustBeNullWhenNotFound -> Failure(INVALID_INPUT). The brick
    // must NOT silently coerce this to Success/needs_enrichment.
    let brick = load_brick();
    let input = json!({
        "audience": Value::Null,
        "company_name": "Ghost",
        "industry": Value::Null,
        "notes": Value::Null,
        "profile_found": false,
        "segment": Value::Null,
        "target_icp": "b2b saas smb creators",
    });
    let envelope = test_envelope(&input);
    let error = as_failure(invoke_and_decode(&brick, &envelope));
    assert_eq!(error.error_class, "INVALID_INPUT");
}

#[test]
fn abi_output_contract_exactly_six_fields_canonical_types() {
    // Verify the Success output map has exactly 6 keys with correct types
    // in each outcome branch.
    let brick = load_brick();
    let envelopes = [
        // Qualified
        test_envelope(&known_app_input(
            "Example SaaS",
            "B2B SaaS",
            "SMB",
            "creators",
            "b2b saas smb creators",
        )),
        // Not qualified
        test_envelope(&known_app_input(
            "Example SaaS",
            "B2B SaaS",
            "SMB",
            "creators",
            "manufacturing enterprise b2c",
        )),
        // Needs enrichment - unknown
        test_envelope(&unknown_app_input("b2b saas smb creators")),
        // Needs enrichment - ICP uninterpretable (known profile)
        test_envelope(&known_app_input(
            "Example SaaS",
            "B2B SaaS",
            "SMB",
            "creators",
            "selling to teams of hobbyists",
        )),
    ];
    let expected = [
        "company_name",
        "icp_match",
        "industry",
        "next_action",
        "reasons",
        "score",
    ];
    for env in &envelopes {
        let output = as_success(invoke_and_decode(&brick, env));
        let fields = match &output {
            CborValue::Map(m) => m,
            other => panic!("output must be Map, got {:?}", other),
        };
        assert_eq!(fields.len(), 6, "output map must have exactly 6 fields");
        let actual: Vec<String> = fields
            .iter()
            .map(|(k, _)| match k {
                CborValue::Text(t) => t.clone(),
                other => panic!("map key must be Text, got {:?}", other),
            })
            .collect();
        for exp in &expected {
            assert!(
                actual.iter().any(|a| a == exp),
                "expected key {} missing; actual keys: {:?}",
                exp,
                actual
            );
        }
        // Canonical types per outcome branch.
        let next_action = match get_map_field(&output, "next_action") {
            CborValue::Text(t) => t.clone(),
            other => panic!("next_action must be Text, got {:?}", other),
        };
        match next_action.as_str() {
            "qualified" | "not_qualified" => {
                // icp_match is Bool; score is Float.
                assert!(matches!(
                    get_map_field(&output, "icp_match"),
                    CborValue::Bool(_)
                ));
                assert!(matches!(
                    get_map_field(&output, "score"),
                    CborValue::Float(_)
                ));
            }
            "needs_enrichment" => {
                // icp_match is Null; score is Null.
                assert!(matches!(
                    get_map_field(&output, "icp_match"),
                    CborValue::Null
                ));
                assert!(matches!(get_map_field(&output, "score"), CborValue::Null));
            }
            other => panic!("unknown next_action: {}", other),
        }
        // reasons is always a nonempty Array of Text strings.
        match get_map_field(&output, "reasons") {
            CborValue::Array(a) => {
                assert!(!a.is_empty());
                for r in a {
                    assert!(matches!(r, CborValue::Text(_)));
                }
            }
            other => panic!("reasons must be Array, got {:?}", other),
        }
    }
}

#[test]
fn abi_determinism_identical_envelope_identical_result_bytes() {
    // For each of the 4 outcome branches, invoke the brick twice on the
    // byte-identical envelope and confirm the result bytes are
    // byte-identical. A fresh Wasmtime store is instantiated each
    // invocation, so this probes result-encoding determinism across
    // separate executions.
    let brick = load_brick();
    let envelopes = [
        test_envelope(&known_app_input(
            "Example SaaS",
            "B2B SaaS",
            "SMB",
            "creators",
            "b2b saas smb creators",
        )),
        test_envelope(&known_app_input(
            "Example SaaS",
            "B2B SaaS",
            "SMB",
            "creators",
            "manufacturing enterprise b2c",
        )),
        test_envelope(&unknown_app_input("x")),
        test_envelope(&known_app_input(
            "Example SaaS",
            "B2B SaaS",
            "SMB",
            "creators",
            "selling to teams of hobbyists",
        )),
    ];
    for env in &envelopes {
        let a = brick
            .invoke(env, MAX_MEM_MB, MAX_OUT_BYTES)
            .expect("invoke #1");
        let b = brick
            .invoke(env, MAX_MEM_MB, MAX_OUT_BYTES)
            .expect("invoke #2");
        assert_eq!(a, b, "repeat invocation must produce byte-identical result");
    }
}

#[test]
fn abi_nested_cbor_adversarial_controlled_rejection() {
    // Deeply-nested CBOR under an UNKNOWN application-map key. Pass 1
    // records the position of each known key via decoder.skip(), which
    // recursively walks nested CBOR values. The envelope-layer skip over
    // envelope.input ALSO recursively walks that same deeply-nested
    // value. Both calls must either succeed (and the parser must then
    // return a controlled UnknownField -> Failure(INVALID_INPUT) via the
    // brick), or we must observe a Wasmtime trap and report its details.
    //
    // Depth 500 is well above anything legitimate while comfortably
    // within WASM's default 1 MiB stack (each skip() frame is < ~500
    // bytes; depth * 2 passes * frame_size ~= 500 KiB worst case).

    let depth = 500usize;
    let brick = load_brick();
    let envelope = hand_rolled_envelope_with_deeply_nested_unknown(depth);
    let bytes_len = envelope.len();

    let result = brick.invoke(&envelope, MAX_MEM_MB, MAX_OUT_BYTES);
    match result {
        Ok(result_bytes) => {
            // Must decode as a Failure(INVALID_INPUT); the parser's
            // UnknownField routes here. If the brick traps mid-skip the
            // outer invoke would have returned Err.
            let decoded = decode_result(&result_bytes).expect("decode result");
            let error = match decoded {
                BrickResult::Failure { error } => error,
                other => panic!(
                    "deeply nested CBOR adversarial case: expected Failure(INVALID_INPUT), \
                     got {:?} at depth={} payload_bytes={}",
                    other.result_type(),
                    depth,
                    bytes_len,
                ),
            };
            assert_eq!(
                error.error_class, "INVALID_INPUT",
                "controlled rejection must use error_class=INVALID_INPUT"
            );
        }
        Err(e) => {
            panic!(
                "WASM trap at nesting depth={}, payload_bytes={}: {:?}",
                depth, bytes_len, e
            );
        }
    }
}

/// Resolver acceptance check for brick 2's manifest.
///
/// Exercises `ncp_runtime::resolver::resolve_brick` on the on-disk
/// `examples/bricks/icp-qualifier/` directory. The manifest omits
/// `artifact.path`, so the resolver must fall through to branch 2 of
/// `select_wasm` ("exactly one `.wasm` file in directory") and pick
/// `icp-qualifier.wasm` automatically. `resolve_brick` also runs
/// `verify_artifact`, which re-hashes the WASM bytes and compares the
/// digest and size against the manifest -- a mismatch returns Err.
#[test]
fn resolver_selects_wasm_without_artifact_path_and_verifies_digest_and_size() {
    let brick_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("CARGO_MANIFEST_DIR has no parent -- runtime/ must live under repo root")
        .join("examples/bricks");

    let resolved = resolve_brick(
        "org.ncp-examples.icp-qualifier",
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
        selected, "icp-qualifier.wasm",
        "resolver must auto-select the single .wasm file when artifact.path is absent"
    );

    // Manifest round-trip.
    assert_eq!(resolved.manifest.brick_id, "org.ncp-examples.icp-qualifier");
    assert_eq!(resolved.manifest.version, "0.1.0");
    assert_eq!(resolved.manifest.artifact.format, "wasm");
    assert_eq!(resolved.manifest.artifact.entrypoint, "invoke");
    assert!(
        resolved.manifest.artifact.path.is_none(),
        "artifact.path must be absent in brick 2's manifest"
    );
    assert_eq!(resolved.manifest.artifact.size_bytes, 30177);
    assert_eq!(
        resolved.manifest.artifact.digest,
        "sha256:f4c50217733a1c889d6d872e4626da5ffe8d86135061162cd8cfd31fd215eab7"
    );

    // verify_artifact already compared size + sha256 against the declared
    // values; reaching this line proves both matched. Mirror the size
    // check explicitly.
    assert_eq!(resolved.wasm_bytes.len(), 30177);

    // Both JSON Schema files on disk at the paths the manifest declares.
    let schema_dir = brick_dir.join("icp-qualifier").join("schemas");
    assert!(
        schema_dir.join("input.schema.json").is_file(),
        "input.schema.json must exist"
    );
    assert!(
        schema_dir.join("output.schema.json").is_file(),
        "output.schema.json must exist"
    );
}

/// Build an envelope whose application-layer map contains an unknown key
/// whose value is `depth` levels of nested CBOR array(1), terminating in
/// a null. The 7 required application fields are all present (null for
/// nullable facts) so that if the UNKNOWN precedence rule flipped and
/// the parser proceeded to Pass 2, it would still find a well-formed
/// shape -- but it must not; UnknownField must win per the parser's
/// declared precedence.
fn hand_rolled_envelope_with_deeply_nested_unknown(depth: usize) -> Vec<u8> {
    // Expected CBOR layout:
    //   0xA1                                  envelope map(1)
    //   0x65 "input"                          key
    //   0xA8                                  app map(8) -- 7 known + 1 unknown
    //   <known fields with null/false/short-string values>
    //   0x67 "mystery"                        unknown key
    //   0x81 * depth  0xF6                    N nested array(1), then null
    //   <remaining known fields>
    //
    // Field insertion order below is wire order; the parser is wire-order-
    // independent for all its semantic errors, so any order is valid.

    let mut buf: Vec<u8> = Vec::with_capacity(depth + 128);

    // envelope: map(1)
    buf.push(0xA1);
    buf.extend_from_slice(&[0x65, b'i', b'n', b'p', b'u', b't']); // "input"

    // app map(8)
    buf.push(0xA8);

    // audience: null
    buf.extend_from_slice(&[0x68, b'a', b'u', b'd', b'i', b'e', b'n', b'c', b'e']);
    buf.push(0xF6);

    // company_name: null
    buf.extend_from_slice(&[
        0x6C, b'c', b'o', b'm', b'p', b'a', b'n', b'y', b'_', b'n', b'a', b'm', b'e',
    ]);
    buf.push(0xF6);

    // industry: null
    buf.extend_from_slice(&[0x68, b'i', b'n', b'd', b'u', b's', b't', b'r', b'y']);
    buf.push(0xF6);

    // mystery: deeply nested array wrapping null
    buf.extend_from_slice(&[0x67, b'm', b'y', b's', b't', b'e', b'r', b'y']);
    // 0x81 is CBOR array(1); `depth` repetitions build `depth` nested array
    // wrappers around a final null.
    buf.extend(std::iter::repeat_n(0x81u8, depth));
    buf.push(0xF6); // final null

    // notes: null
    buf.extend_from_slice(&[0x65, b'n', b'o', b't', b'e', b's']);
    buf.push(0xF6);

    // profile_found: false
    buf.extend_from_slice(&[
        0x6D, b'p', b'r', b'o', b'f', b'i', b'l', b'e', b'_', b'f', b'o', b'u', b'n', b'd',
    ]);
    buf.push(0xF4);

    // segment: null
    buf.extend_from_slice(&[0x67, b's', b'e', b'g', b'm', b'e', b'n', b't']);
    buf.push(0xF6);

    // target_icp: "x"
    buf.extend_from_slice(&[
        0x6A, b't', b'a', b'r', b'g', b'e', b't', b'_', b'i', b'c', b'p',
    ]);
    buf.extend_from_slice(&[0x61, b'x']);

    buf
}
