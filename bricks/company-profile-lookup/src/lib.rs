// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

//! NCP company-profile-lookup Brick (v0.1).
//!
//! Offline fictional-catalog resolver for the lead-qualification example
//! graph (issue #29). Combines CBOR input parsing with strict offline URL
//! normalization and a 3-entry fictional profile catalog under IETF-reserved
//! example.com subdomains.
//!
//! Code is organized into three sections:
//! - Section 1: CBOR envelope parsing + application-layer validation + error types
//! - Section 2: URL normalization (strict, offline, deterministic) + catalog lookup
//! - Section 3: Output encoding + WASM Model B ABI exports (wasm32-only)
//!
//! Envelope contract (verified against runtime/src/envelope.rs; the runtime
//! normalizes raw and wrapped adopter input to the wrapped form before
//! dispatching to any Brick): top-level definite-length CBOR map containing
//! an `input` key plus other envelope keys (graph_id, node_id, trigger info).
//!
//! Determinism: same input bytes produce byte-identical result bytes. Fully
//! offline: never performs DNS, HTTP, or any network operation.

#![cfg_attr(target_arch = "wasm32", no_std)]
#![cfg_attr(not(target_arch = "wasm32"), allow(dead_code))]

extern crate alloc;

use alloc::string::{String, ToString};
use alloc::vec::Vec;
use minicbor::data::Type;

// ═════════════════════════════════════════════════════════════════════════
// Section 1 -- CBOR parsing + application validation + error types
// ═════════════════════════════════════════════════════════════════════════
//
// Deterministic error precedence (position-invariant across CBOR key order):
//   1. Envelope structural: MalformedCbor, IndefiniteLengthMap, InputNotMap,
//      TrailingBytes, DuplicateInputField, MissingInputField.
//   2. Application structural: TooManyApplicationFields, IndefiniteLengthMap.
//   3. DuplicateApplicationKey.
//   4. UnknownApplicationField(<name>).
//   5. MissingCompanyUrl -> MissingTargetIcp (schema order).
//   6. Type check in schema order (company_url, target_icp, notes):
//      WrongType -> *NotString; IndefiniteLengthString; MalformedCbor
//      (truncated body or invalid UTF-8 after a valid String header).
//   7. Length check in schema order (Empty / TooLong).

// ── Error type and messages ──────────────────────────────────────────────

/// Internal parsing + validation error. All variants map to
/// `Failure(INVALID_INPUT)` at the Brick-output level (Section 3). Variant
/// distinction supports deterministic-precedence tests; `message()` is the
/// user-facing string.
#[derive(Debug, Clone, PartialEq)]
pub(crate) enum InputError {
    // Envelope-layer
    MalformedCbor,
    TrailingBytes,
    IndefiniteLengthMap,
    MissingInputField,
    DuplicateInputField,
    InputNotMap,

    // Application-layer
    TooManyApplicationFields,
    DuplicateApplicationKey,
    UnknownApplicationField(String),
    MissingCompanyUrl,
    MissingTargetIcp,
    CompanyUrlNotString,
    TargetIcpNotString,
    NotesNotStringOrNull,
    IndefiniteLengthString,
    CompanyUrlEmpty,
    CompanyUrlTooLong,
    TargetIcpEmpty,
    TargetIcpTooLong,
    NotesTooLong,

    // URL normalization (Section 2)
    NonAsciiHost,
    InvalidHost,
    UnsupportedScheme,
    UserinfoNotAllowed,
    PortNotAllowed,
}

impl InputError {
    pub(crate) fn message(&self) -> String {
        match self {
            InputError::MalformedCbor => "envelope: malformed CBOR".to_string(),
            InputError::TrailingBytes => "envelope: trailing bytes after outer map".to_string(),
            InputError::IndefiniteLengthMap => {
                "envelope: indefinite-length maps not supported".to_string()
            }
            InputError::MissingInputField => "envelope: missing input field".to_string(),
            InputError::DuplicateInputField => "envelope: duplicate input field".to_string(),
            InputError::InputNotMap => "envelope: input must be a CBOR map".to_string(),

            InputError::TooManyApplicationFields => {
                "input: too many application fields (max 32)".to_string()
            }
            InputError::DuplicateApplicationKey => "input: duplicate application key".to_string(),
            InputError::UnknownApplicationField(name) => {
                let mut s = String::from("input: unknown application field '");
                s.push_str(name);
                s.push('\'');
                s
            }
            InputError::MissingCompanyUrl => {
                "input: missing required field company_url".to_string()
            }
            InputError::MissingTargetIcp => "input: missing required field target_icp".to_string(),
            InputError::CompanyUrlNotString => {
                "company_url: must be a CBOR text string".to_string()
            }
            InputError::TargetIcpNotString => "target_icp: must be a CBOR text string".to_string(),
            InputError::NotesNotStringOrNull => {
                "notes: must be a CBOR text string or null".to_string()
            }
            InputError::IndefiniteLengthString => {
                "input: indefinite-length text strings not supported".to_string()
            }
            InputError::CompanyUrlEmpty => "company_url: empty".to_string(),
            InputError::CompanyUrlTooLong => "company_url: exceeds 2048 characters".to_string(),
            InputError::TargetIcpEmpty => "target_icp: empty".to_string(),
            InputError::TargetIcpTooLong => "target_icp: exceeds 1024 characters".to_string(),
            InputError::NotesTooLong => "notes: exceeds 1024 characters".to_string(),

            InputError::NonAsciiHost => "company_url: non-ASCII character".to_string(),
            InputError::InvalidHost => "company_url: invalid hostname syntax".to_string(),
            InputError::UnsupportedScheme => "company_url: unsupported scheme".to_string(),
            InputError::UserinfoNotAllowed => "company_url: userinfo not allowed".to_string(),
            InputError::PortNotAllowed => "company_url: port not allowed".to_string(),
        }
    }
}

// ── Validated input + constants ──────────────────────────────────────────

/// Validated application input returned by `parse_input` on success.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct ParsedInput {
    pub(crate) company_url: String,
    pub(crate) target_icp: String,
    pub(crate) notes: Option<String>,
}

pub(crate) const MAX_COMPANY_URL_LEN: usize = 2048;
pub(crate) const MAX_TARGET_ICP_LEN: usize = 1024;
pub(crate) const MAX_NOTES_LEN: usize = 1024;
pub(crate) const MAX_APPLICATION_FIELDS: u64 = 32;
const MAX_UNKNOWN_KEY_DIAGNOSTIC: usize = 64;

// ── Envelope-layer extraction ────────────────────────────────────────────

fn extract_input_bytes(envelope: &[u8]) -> Result<&[u8], InputError> {
    let mut decoder = minicbor::Decoder::new(envelope);

    match decoder.datatype() {
        Ok(Type::Map) => {}
        Ok(Type::MapIndef) => return Err(InputError::IndefiniteLengthMap),
        Ok(Type::Break) => return Err(InputError::MalformedCbor),
        Ok(_) => return Err(InputError::InputNotMap),
        Err(_) => return Err(InputError::MalformedCbor),
    }

    let len = decoder
        .map()
        .map_err(|_| InputError::MalformedCbor)?
        .ok_or(InputError::IndefiniteLengthMap)?;

    let mut input_bytes: Option<&[u8]> = None;

    for _ in 0..len {
        if decoder.datatype().map_err(|_| InputError::MalformedCbor)? != Type::String {
            return Err(InputError::MalformedCbor);
        }
        let key = decoder.str().map_err(|_| InputError::MalformedCbor)?;
        if key == "input" {
            if input_bytes.is_some() {
                return Err(InputError::DuplicateInputField);
            }
            let start = decoder.position();
            decoder.skip().map_err(|_| InputError::MalformedCbor)?;
            let end = decoder.position();
            input_bytes = Some(&envelope[start..end]);
        } else {
            decoder.skip().map_err(|_| InputError::MalformedCbor)?;
        }
    }

    if decoder.position() < envelope.len() {
        return Err(InputError::TrailingBytes);
    }

    input_bytes.ok_or(InputError::MissingInputField)
}

// ── Strict single-value decoders (borrowed, no allocation) ───────────────

fn decode_string_borrowed(bytes: &[u8], type_err: InputError) -> Result<&str, InputError> {
    let mut decoder = minicbor::Decoder::new(bytes);
    match decoder.datatype() {
        Ok(Type::String) => decoder.str().map_err(|_| InputError::MalformedCbor),
        Ok(Type::StringIndef) => Err(InputError::IndefiniteLengthString),
        Ok(_) => Err(type_err),
        Err(_) => Err(InputError::MalformedCbor),
    }
}

fn decode_optional_string_borrowed(bytes: &[u8]) -> Result<Option<&str>, InputError> {
    let mut decoder = minicbor::Decoder::new(bytes);
    match decoder.datatype() {
        Ok(Type::Null) => Ok(None),
        Ok(Type::String) => decoder
            .str()
            .map(Some)
            .map_err(|_| InputError::MalformedCbor),
        Ok(Type::StringIndef) => Err(InputError::IndefiniteLengthString),
        Ok(_) => Err(InputError::NotesNotStringOrNull),
        Err(_) => Err(InputError::MalformedCbor),
    }
}

// ── Application-layer validation ─────────────────────────────────────────

fn parse_application(input_bytes: &[u8]) -> Result<ParsedInput, InputError> {
    let mut decoder = minicbor::Decoder::new(input_bytes);

    match decoder.datatype() {
        Ok(Type::Map) => {}
        Ok(Type::MapIndef) => return Err(InputError::IndefiniteLengthMap),
        Ok(Type::Break) => return Err(InputError::MalformedCbor),
        Ok(_) => return Err(InputError::InputNotMap),
        Err(_) => return Err(InputError::MalformedCbor),
    }

    let len = decoder
        .map()
        .map_err(|_| InputError::MalformedCbor)?
        .ok_or(InputError::IndefiniteLengthMap)?;

    if len > MAX_APPLICATION_FIELDS {
        return Err(InputError::TooManyApplicationFields);
    }

    let mut company_url_bytes: Option<&[u8]> = None;
    let mut target_icp_bytes: Option<&[u8]> = None;
    let mut notes_bytes: Option<&[u8]> = None;
    let mut has_duplicate = false;
    let mut first_unknown: Option<String> = None;

    for _ in 0..len {
        if decoder.datatype().map_err(|_| InputError::MalformedCbor)? != Type::String {
            return Err(InputError::MalformedCbor);
        }
        let key: &str = decoder.str().map_err(|_| InputError::MalformedCbor)?;
        let value_start = decoder.position();
        decoder.skip().map_err(|_| InputError::MalformedCbor)?;
        let value_end = decoder.position();
        let value_slice = &input_bytes[value_start..value_end];

        match key {
            "company_url" => {
                if company_url_bytes.is_some() {
                    has_duplicate = true;
                } else {
                    company_url_bytes = Some(value_slice);
                }
            }
            "target_icp" => {
                if target_icp_bytes.is_some() {
                    has_duplicate = true;
                } else {
                    target_icp_bytes = Some(value_slice);
                }
            }
            "notes" => {
                if notes_bytes.is_some() {
                    has_duplicate = true;
                } else {
                    notes_bytes = Some(value_slice);
                }
            }
            _ => {
                if first_unknown.is_none() {
                    let truncated: String = key.chars().take(MAX_UNKNOWN_KEY_DIAGNOSTIC).collect();
                    first_unknown = Some(truncated);
                }
            }
        }
    }

    if has_duplicate {
        return Err(InputError::DuplicateApplicationKey);
    }
    if let Some(name) = first_unknown {
        return Err(InputError::UnknownApplicationField(name));
    }
    let company_url_slice = company_url_bytes.ok_or(InputError::MissingCompanyUrl)?;
    let target_icp_slice = target_icp_bytes.ok_or(InputError::MissingTargetIcp)?;

    let company_url_str =
        decode_string_borrowed(company_url_slice, InputError::CompanyUrlNotString)?;
    let target_icp_str = decode_string_borrowed(target_icp_slice, InputError::TargetIcpNotString)?;
    let notes_opt: Option<&str> = match notes_bytes {
        None => None,
        Some(slice) => decode_optional_string_borrowed(slice)?,
    };

    if company_url_str.is_empty() {
        return Err(InputError::CompanyUrlEmpty);
    }
    if company_url_str.chars().count() > MAX_COMPANY_URL_LEN {
        return Err(InputError::CompanyUrlTooLong);
    }
    if target_icp_str.is_empty() {
        return Err(InputError::TargetIcpEmpty);
    }
    if target_icp_str.chars().count() > MAX_TARGET_ICP_LEN {
        return Err(InputError::TargetIcpTooLong);
    }
    if let Some(n) = notes_opt {
        if n.chars().count() > MAX_NOTES_LEN {
            return Err(InputError::NotesTooLong);
        }
    }

    Ok(ParsedInput {
        company_url: company_url_str.to_string(),
        target_icp: target_icp_str.to_string(),
        notes: notes_opt.map(|s| s.to_string()),
    })
}

pub(crate) fn parse_input(envelope: &[u8]) -> Result<ParsedInput, InputError> {
    let input_bytes = extract_input_bytes(envelope)?;
    parse_application(input_bytes)
}

// ── Section 1 host-side unit tests ───────────────────────────────────────

#[cfg(all(test, not(target_arch = "wasm32")))]
mod section1_tests {
    use super::*;
    use minicbor::Encoder;

    fn envelope<F>(build_input: F) -> Vec<u8>
    where
        F: FnOnce(&mut Encoder<&mut Vec<u8>>),
    {
        let mut buf = Vec::new();
        let mut enc = Encoder::new(&mut buf);
        enc.map(1).unwrap();
        enc.str("input").unwrap();
        build_input(&mut enc);
        buf
    }

    fn good_input<F>(fields: usize, build_entries: F) -> Vec<u8>
    where
        F: FnOnce(&mut Encoder<&mut Vec<u8>>),
    {
        envelope(|enc| {
            enc.map(fields as u64).unwrap();
            build_entries(enc);
        })
    }

    fn fixture_with_corrupted_value(
        before: &[(&str, &str)],
        corrupted_key: &str,
        corrupted_value_raw: &[u8],
        after: &[(&str, &str)],
    ) -> Vec<u8> {
        let total = before.len() + 1 + after.len();
        let mut buf = Vec::new();
        {
            let mut enc = Encoder::new(&mut buf);
            enc.map(1).unwrap();
            enc.str("input").unwrap();
            enc.map(total as u64).unwrap();
            for &(k, v) in before {
                enc.str(k).unwrap();
                enc.str(v).unwrap();
            }
            enc.str(corrupted_key).unwrap();
        }
        buf.extend_from_slice(corrupted_value_raw);
        {
            let mut enc = Encoder::new(&mut buf);
            for &(k, v) in after {
                enc.str(k).unwrap();
                enc.str(v).unwrap();
            }
        }
        buf
    }

    #[test]
    fn parses_minimal_valid_input() {
        let bytes = good_input(2, |e| {
            e.str("company_url").unwrap();
            e.str("https://example.com").unwrap();
            e.str("target_icp").unwrap();
            e.str("b2b saas smb creators").unwrap();
        });
        let parsed = parse_input(&bytes).unwrap();
        assert_eq!(parsed.company_url, "https://example.com");
        assert_eq!(parsed.target_icp, "b2b saas smb creators");
        assert_eq!(parsed.notes, None);
    }

    #[test]
    fn notes_absent_yields_none() {
        let bytes = good_input(2, |e| {
            e.str("company_url").unwrap();
            e.str("https://example.com").unwrap();
            e.str("target_icp").unwrap();
            e.str("x").unwrap();
        });
        assert_eq!(parse_input(&bytes).unwrap().notes, None);
    }

    #[test]
    fn notes_explicit_null_yields_none() {
        let bytes = good_input(3, |e| {
            e.str("company_url").unwrap();
            e.str("https://example.com").unwrap();
            e.str("target_icp").unwrap();
            e.str("x").unwrap();
            e.str("notes").unwrap();
            e.null().unwrap();
        });
        assert_eq!(parse_input(&bytes).unwrap().notes, None);
    }

    #[test]
    fn notes_string_passed_through() {
        let bytes = good_input(3, |e| {
            e.str("company_url").unwrap();
            e.str("https://example.com").unwrap();
            e.str("target_icp").unwrap();
            e.str("x").unwrap();
            e.str("notes").unwrap();
            e.str("hello").unwrap();
        });
        assert_eq!(
            parse_input(&bytes).unwrap().notes,
            Some("hello".to_string())
        );
    }

    #[test]
    fn tolerates_unknown_envelope_keys() {
        let mut buf = Vec::new();
        let mut enc = Encoder::new(&mut buf);
        enc.map(2).unwrap();
        enc.str("input").unwrap();
        enc.map(2).unwrap();
        enc.str("company_url").unwrap();
        enc.str("https://example.com").unwrap();
        enc.str("target_icp").unwrap();
        enc.str("x").unwrap();
        enc.str("graph_id").unwrap();
        enc.str("ignored").unwrap();
        assert_eq!(
            parse_input(&buf).unwrap().company_url,
            "https://example.com"
        );
    }

    #[test]
    fn rejects_envelope_malformed_cbor() {
        assert_eq!(parse_input(&[0xFF]), Err(InputError::MalformedCbor));
    }

    #[test]
    fn rejects_envelope_not_a_map() {
        let mut buf = Vec::new();
        Encoder::new(&mut buf).str("not a map").unwrap();
        assert_eq!(parse_input(&buf), Err(InputError::InputNotMap));
    }

    #[test]
    fn rejects_envelope_indefinite_length_map() {
        assert_eq!(
            parse_input(&[0xBF, 0xFF]),
            Err(InputError::IndefiniteLengthMap)
        );
    }

    #[test]
    fn rejects_missing_input_field() {
        let mut buf = Vec::new();
        let mut enc = Encoder::new(&mut buf);
        enc.map(1).unwrap();
        enc.str("not_input").unwrap();
        enc.str("v").unwrap();
        assert_eq!(parse_input(&buf), Err(InputError::MissingInputField));
    }

    #[test]
    fn rejects_duplicate_input_field() {
        let mut buf = Vec::new();
        let mut enc = Encoder::new(&mut buf);
        enc.map(2).unwrap();
        enc.str("input").unwrap();
        enc.map(0).unwrap();
        enc.str("input").unwrap();
        enc.map(0).unwrap();
        assert_eq!(parse_input(&buf), Err(InputError::DuplicateInputField));
    }

    #[test]
    fn rejects_trailing_bytes_after_envelope() {
        let mut bytes = good_input(2, |e| {
            e.str("company_url").unwrap();
            e.str("https://example.com").unwrap();
            e.str("target_icp").unwrap();
            e.str("x").unwrap();
        });
        bytes.push(0x00);
        assert_eq!(parse_input(&bytes), Err(InputError::TrailingBytes));
    }

    #[test]
    fn rejects_malformed_cbor_in_unknown_envelope_field() {
        let mut buf = Vec::new();
        {
            let mut enc = Encoder::new(&mut buf);
            enc.map(2).unwrap();
            enc.str("input").unwrap();
            enc.map(2).unwrap();
            enc.str("company_url").unwrap();
            enc.str("https://example.com").unwrap();
            enc.str("target_icp").unwrap();
            enc.str("x").unwrap();
            enc.str("future").unwrap();
        }
        buf.push(0xA5);
        assert_eq!(parse_input(&buf), Err(InputError::MalformedCbor));
    }

    #[test]
    fn rejects_input_not_map() {
        let bytes = envelope(|e| {
            e.str("not a map").unwrap();
        });
        assert_eq!(parse_input(&bytes), Err(InputError::InputNotMap));
    }

    #[test]
    fn rejects_application_indefinite_length_map() {
        let mut buf = Vec::new();
        {
            let mut enc = Encoder::new(&mut buf);
            enc.map(1).unwrap();
            enc.str("input").unwrap();
        }
        buf.push(0xBF);
        buf.push(0xFF);
        assert_eq!(parse_input(&buf), Err(InputError::IndefiniteLengthMap));
    }

    #[test]
    fn rejects_too_many_application_fields() {
        let bytes = envelope(|e| {
            e.map(33).unwrap();
            for i in 0..33u32 {
                let k = alloc::format!("k{}", i);
                e.str(&k).unwrap();
                e.str("v").unwrap();
            }
        });
        assert_eq!(
            parse_input(&bytes),
            Err(InputError::TooManyApplicationFields)
        );
    }

    #[test]
    fn rejects_duplicate_application_key() {
        let bytes = good_input(3, |e| {
            e.str("company_url").unwrap();
            e.str("a").unwrap();
            e.str("company_url").unwrap();
            e.str("b").unwrap();
            e.str("target_icp").unwrap();
            e.str("x").unwrap();
        });
        assert_eq!(
            parse_input(&bytes),
            Err(InputError::DuplicateApplicationKey)
        );
    }

    #[test]
    fn rejects_unknown_application_field_names_the_key() {
        let bytes = good_input(3, |e| {
            e.str("company_url").unwrap();
            e.str("https://example.com").unwrap();
            e.str("target_icp").unwrap();
            e.str("x").unwrap();
            e.str("mystery_key").unwrap();
            e.str("boom").unwrap();
        });
        let err = parse_input(&bytes).unwrap_err();
        assert_eq!(
            err,
            InputError::UnknownApplicationField("mystery_key".to_string())
        );
        assert_eq!(
            err.message(),
            "input: unknown application field 'mystery_key'"
        );
    }

    #[test]
    fn unknown_key_diagnostic_truncated_to_64_chars() {
        let long_key: String = (0..100).map(|_| 'x').collect();
        let bytes = good_input(3, |e| {
            e.str("company_url").unwrap();
            e.str("https://example.com").unwrap();
            e.str("target_icp").unwrap();
            e.str("x").unwrap();
            e.str(&long_key).unwrap();
            e.str("boom").unwrap();
        });
        let err = parse_input(&bytes).unwrap_err();
        let expected: String = (0..MAX_UNKNOWN_KEY_DIAGNOSTIC).map(|_| 'x').collect();
        assert_eq!(err, InputError::UnknownApplicationField(expected));
    }

    #[test]
    fn rejects_missing_company_url() {
        let bytes = good_input(1, |e| {
            e.str("target_icp").unwrap();
            e.str("x").unwrap();
        });
        assert_eq!(parse_input(&bytes), Err(InputError::MissingCompanyUrl));
    }

    #[test]
    fn rejects_missing_target_icp() {
        let bytes = good_input(1, |e| {
            e.str("company_url").unwrap();
            e.str("https://example.com").unwrap();
        });
        assert_eq!(parse_input(&bytes), Err(InputError::MissingTargetIcp));
    }

    #[test]
    fn rejects_company_url_not_string() {
        let bytes = good_input(2, |e| {
            e.str("company_url").unwrap();
            e.u8(42).unwrap();
            e.str("target_icp").unwrap();
            e.str("x").unwrap();
        });
        assert_eq!(parse_input(&bytes), Err(InputError::CompanyUrlNotString));
    }

    #[test]
    fn rejects_target_icp_not_string() {
        let bytes = good_input(2, |e| {
            e.str("company_url").unwrap();
            e.str("https://example.com").unwrap();
            e.str("target_icp").unwrap();
            e.bool(true).unwrap();
        });
        assert_eq!(parse_input(&bytes), Err(InputError::TargetIcpNotString));
    }

    #[test]
    fn rejects_notes_wrong_type() {
        let bytes = good_input(3, |e| {
            e.str("company_url").unwrap();
            e.str("https://example.com").unwrap();
            e.str("target_icp").unwrap();
            e.str("x").unwrap();
            e.str("notes").unwrap();
            e.u8(0).unwrap();
        });
        assert_eq!(parse_input(&bytes), Err(InputError::NotesNotStringOrNull));
    }

    #[test]
    fn rejects_indefinite_length_string_as_value() {
        let bytes =
            fixture_with_corrupted_value(&[], "company_url", &[0x7F, 0xFF], &[("target_icp", "x")]);
        assert_eq!(parse_input(&bytes), Err(InputError::IndefiniteLengthString));
    }

    #[test]
    fn malformed_utf8_in_string_body_is_malformed_not_wrong_type() {
        let bytes =
            fixture_with_corrupted_value(&[], "company_url", &[0x61, 0xFF], &[("target_icp", "x")]);
        assert_eq!(parse_input(&bytes), Err(InputError::MalformedCbor));
    }

    #[test]
    fn malformed_body_on_target_icp_is_malformed() {
        let bytes = fixture_with_corrupted_value(
            &[("company_url", "https://example.com")],
            "target_icp",
            &[0x61, 0xFE],
            &[],
        );
        assert_eq!(parse_input(&bytes), Err(InputError::MalformedCbor));
    }

    #[test]
    fn rejects_empty_company_url() {
        let bytes = good_input(2, |e| {
            e.str("company_url").unwrap();
            e.str("").unwrap();
            e.str("target_icp").unwrap();
            e.str("x").unwrap();
        });
        assert_eq!(parse_input(&bytes), Err(InputError::CompanyUrlEmpty));
    }

    #[test]
    fn rejects_company_url_too_long() {
        let long: String = (0..2049).map(|_| 'a').collect();
        let bytes = good_input(2, |e| {
            e.str("company_url").unwrap();
            e.str(&long).unwrap();
            e.str("target_icp").unwrap();
            e.str("x").unwrap();
        });
        assert_eq!(parse_input(&bytes), Err(InputError::CompanyUrlTooLong));
    }

    #[test]
    fn rejects_empty_target_icp() {
        let bytes = good_input(2, |e| {
            e.str("company_url").unwrap();
            e.str("https://example.com").unwrap();
            e.str("target_icp").unwrap();
            e.str("").unwrap();
        });
        assert_eq!(parse_input(&bytes), Err(InputError::TargetIcpEmpty));
    }

    #[test]
    fn rejects_target_icp_too_long() {
        let long: String = (0..1025).map(|_| 'a').collect();
        let bytes = good_input(2, |e| {
            e.str("company_url").unwrap();
            e.str("https://example.com").unwrap();
            e.str("target_icp").unwrap();
            e.str(&long).unwrap();
        });
        assert_eq!(parse_input(&bytes), Err(InputError::TargetIcpTooLong));
    }

    #[test]
    fn rejects_notes_too_long() {
        let long: String = (0..1025).map(|_| 'n').collect();
        let bytes = good_input(3, |e| {
            e.str("company_url").unwrap();
            e.str("https://example.com").unwrap();
            e.str("target_icp").unwrap();
            e.str("x").unwrap();
            e.str("notes").unwrap();
            e.str(&long).unwrap();
        });
        assert_eq!(parse_input(&bytes), Err(InputError::NotesTooLong));
    }

    #[test]
    fn precedence_duplicate_beats_unknown() {
        let bytes = good_input(3, |e| {
            e.str("company_url").unwrap();
            e.str("a").unwrap();
            e.str("company_url").unwrap();
            e.str("b").unwrap();
            e.str("mystery").unwrap();
            e.str("x").unwrap();
        });
        assert_eq!(
            parse_input(&bytes),
            Err(InputError::DuplicateApplicationKey)
        );
    }

    #[test]
    fn precedence_unknown_beats_missing_required() {
        let bytes = good_input(2, |e| {
            e.str("company_url").unwrap();
            e.str("https://example.com").unwrap();
            e.str("mystery").unwrap();
            e.str("x").unwrap();
        });
        assert_eq!(
            parse_input(&bytes),
            Err(InputError::UnknownApplicationField("mystery".to_string()))
        );
    }

    #[test]
    fn precedence_target_icp_type_error_beats_company_url_length() {
        let long: String = (0..2049).map(|_| 'a').collect();
        let bytes = good_input(2, |e| {
            e.str("company_url").unwrap();
            e.str(&long).unwrap();
            e.str("target_icp").unwrap();
            e.u8(42).unwrap();
        });
        assert_eq!(parse_input(&bytes), Err(InputError::TargetIcpNotString));
    }

    #[test]
    fn precedence_company_url_length_beats_target_icp_length() {
        let long: String = (0..2049).map(|_| 'a').collect();
        let longer: String = (0..1025).map(|_| 'b').collect();
        let bytes = good_input(2, |e| {
            e.str("company_url").unwrap();
            e.str(&long).unwrap();
            e.str("target_icp").unwrap();
            e.str(&longer).unwrap();
        });
        assert_eq!(parse_input(&bytes), Err(InputError::CompanyUrlTooLong));
    }

    #[test]
    fn malformed_inputs_never_panic() {
        for b in 0u16..=255 {
            let bytes: Vec<u8> = alloc::vec![b as u8];
            let _ = parse_input(&bytes);
        }
        for seed in 0u64..64 {
            for len in 2..32 {
                let mut bytes = Vec::with_capacity(len);
                let mut x = seed.wrapping_mul(0x9E3779B97F4A7C15);
                for _ in 0..len {
                    x = x
                        .wrapping_mul(6364136223846793005)
                        .wrapping_add(1442695040888963407);
                    bytes.push((x >> 56) as u8);
                }
                let _ = parse_input(&bytes);
            }
        }
    }
}

// ═════════════════════════════════════════════════════════════════════════
// Section 2 -- URL normalization + fictional catalog
// ═════════════════════════════════════════════════════════════════════════

/// Resolved profile for a known fictional company. Not a real-company fact.
#[derive(Debug, Clone, Copy, PartialEq)]
pub(crate) struct CatalogEntry {
    pub(crate) company_name: &'static str,
    pub(crate) industry: &'static str,
    pub(crate) segment: &'static str,
    pub(crate) audience: &'static str,
}

const CATALOG: &[(&str, CatalogEntry)] = &[
    (
        "example.com",
        CatalogEntry {
            company_name: "Example SaaS",
            industry: "B2B SaaS",
            segment: "SMB",
            audience: "creators",
        },
    ),
    (
        "factory.example.com",
        CatalogEntry {
            company_name: "Factory Example",
            industry: "manufacturing",
            segment: "enterprise",
            audience: "B2B",
        },
    ),
    (
        "shop.example.com",
        CatalogEntry {
            company_name: "Shop Example",
            industry: "retail",
            segment: "SMB",
            audience: "B2C",
        },
    ),
];

pub(crate) fn lookup_profile(normalized_host: &str) -> Option<&'static CatalogEntry> {
    CATALOG
        .iter()
        .find(|(host, _)| *host == normalized_host)
        .map(|(_, entry)| entry)
}

pub(crate) fn normalize_url(input: &str) -> Result<String, InputError> {
    if input.is_empty() {
        return Err(InputError::CompanyUrlEmpty);
    }
    if input.chars().count() > MAX_COMPANY_URL_LEN {
        return Err(InputError::CompanyUrlTooLong);
    }

    for b in input.bytes() {
        if b > 0x7F {
            return Err(InputError::NonAsciiHost);
        }
        if b < 0x20 || b == 0x7F || b == b' ' || b == b'\\' || b == b'[' || b == b']' {
            return Err(InputError::InvalidHost);
        }
    }

    let (remainder, has_scheme) = detect_and_strip_scheme(input)?;

    let authority = if has_scheme {
        extract_authority(remainder)
    } else {
        if remainder.contains('/')
            || remainder.contains('?')
            || remainder.contains('#')
            || remainder.contains(':')
        {
            return Err(InputError::InvalidHost);
        }
        remainder
    };

    if authority.contains('@') {
        return Err(InputError::UserinfoNotAllowed);
    }
    if authority.contains(':') {
        return Err(InputError::PortNotAllowed);
    }

    let mut normalized = String::with_capacity(authority.len());
    for b in authority.bytes() {
        let out = if b.is_ascii_uppercase() { b + 32 } else { b };
        normalized.push(out as char);
    }

    if normalized.ends_with("..") {
        return Err(InputError::InvalidHost);
    }
    if normalized.ends_with('.') {
        normalized.pop();
    }

    if normalized.starts_with("www.") {
        normalized = normalized[4..].to_string();
    }

    validate_hostname(&normalized)?;

    Ok(normalized)
}

fn detect_and_strip_scheme(input: &str) -> Result<(&str, bool), InputError> {
    if input.len() >= 8 && input[..8].eq_ignore_ascii_case("https://") {
        Ok((&input[8..], true))
    } else if input.len() >= 7 && input[..7].eq_ignore_ascii_case("http://") {
        Ok((&input[7..], true))
    } else if (input.len() >= 6 && input[..6].eq_ignore_ascii_case("https:"))
        || (input.len() >= 5 && input[..5].eq_ignore_ascii_case("http:"))
    {
        Err(InputError::InvalidHost)
    } else if input.contains("://") {
        Err(InputError::UnsupportedScheme)
    } else {
        Ok((input, false))
    }
}

fn extract_authority(remainder: &str) -> &str {
    let cut = remainder
        .char_indices()
        .find(|(_, c)| matches!(c, '/' | '?' | '#'))
        .map(|(i, _)| i)
        .unwrap_or(remainder.len());
    &remainder[..cut]
}

fn validate_hostname(host: &str) -> Result<(), InputError> {
    if host.is_empty() {
        return Err(InputError::InvalidHost);
    }
    if host.len() > 253 {
        return Err(InputError::InvalidHost);
    }

    let mut label_count: usize = 0;
    let mut has_non_digit_label = false;

    for label in host.split('.') {
        label_count += 1;
        if label.is_empty() {
            return Err(InputError::InvalidHost);
        }
        if label.len() > 63 {
            return Err(InputError::InvalidHost);
        }
        let bytes = label.as_bytes();
        if bytes[0] == b'-' || bytes[bytes.len() - 1] == b'-' {
            return Err(InputError::InvalidHost);
        }

        let mut label_is_all_digits = true;
        for &b in bytes {
            let is_letter = b.is_ascii_lowercase();
            let is_digit = b.is_ascii_digit();
            let is_hyphen = b == b'-';
            if !is_letter && !is_digit && !is_hyphen {
                return Err(InputError::InvalidHost);
            }
            if !is_digit {
                label_is_all_digits = false;
            }
        }
        if !label_is_all_digits {
            has_non_digit_label = true;
        }
    }

    if label_count < 2 {
        return Err(InputError::InvalidHost);
    }
    if !has_non_digit_label {
        return Err(InputError::InvalidHost);
    }

    Ok(())
}

// ── Section 2 host-side unit tests ───────────────────────────────────────

#[cfg(all(test, not(target_arch = "wasm32")))]
mod section2_tests {
    use super::*;

    #[test]
    fn url_01_https_bare_host() {
        assert_eq!(normalize_url("https://example.com").unwrap(), "example.com");
    }
    #[test]
    fn url_02_http_with_path() {
        assert_eq!(
            normalize_url("http://example.com/about").unwrap(),
            "example.com"
        );
    }
    #[test]
    fn url_03_https_path_query_frag() {
        assert_eq!(
            normalize_url("https://example.com/a?b=c#d").unwrap(),
            "example.com"
        );
    }
    #[test]
    fn url_04_https_query_only() {
        assert_eq!(
            normalize_url("https://example.com?x=1").unwrap(),
            "example.com"
        );
    }
    #[test]
    fn url_05_https_fragment_only() {
        assert_eq!(
            normalize_url("https://example.com#section").unwrap(),
            "example.com"
        );
    }
    #[test]
    fn url_06_bare_host() {
        assert_eq!(normalize_url("example.com").unwrap(), "example.com");
    }
    #[test]
    fn url_07_mixed_case_scheme_and_host() {
        assert_eq!(normalize_url("HTTPS://Example.Com").unwrap(), "example.com");
    }
    #[test]
    fn url_08_strip_leading_www() {
        assert_eq!(
            normalize_url("https://www.example.com").unwrap(),
            "example.com"
        );
    }
    #[test]
    fn url_09_subdomain_preserved() {
        assert_eq!(
            normalize_url("https://factory.example.com").unwrap(),
            "factory.example.com"
        );
    }
    #[test]
    fn url_10_strip_trailing_dot() {
        assert_eq!(
            normalize_url("https://example.com.").unwrap(),
            "example.com"
        );
    }
    #[test]
    fn url_11_uppercase_with_trailing_dot() {
        assert_eq!(
            normalize_url("https://EXAMPLE.COM.").unwrap(),
            "example.com"
        );
    }
    #[test]
    fn url_12_only_first_www_stripped() {
        assert_eq!(
            normalize_url("https://www.www.example.com").unwrap(),
            "www.example.com"
        );
    }

    #[test]
    fn url_13_empty() {
        assert_eq!(normalize_url(""), Err(InputError::CompanyUrlEmpty));
    }

    #[test]
    fn url_14_too_long() {
        let s: String = (0..2049).map(|_| 'a').collect();
        assert_eq!(normalize_url(&s), Err(InputError::CompanyUrlTooLong));
    }

    #[test]
    fn url_15_https_empty_host() {
        assert_eq!(normalize_url("https://"), Err(InputError::InvalidHost));
    }
    #[test]
    fn url_16_bare_host_with_path() {
        assert_eq!(
            normalize_url("example.com/about"),
            Err(InputError::InvalidHost)
        );
    }
    #[test]
    fn url_17_https_without_slashslash() {
        assert_eq!(
            normalize_url("https:example.com"),
            Err(InputError::InvalidHost)
        );
    }
    #[test]
    fn url_18_ftp_rejected() {
        assert_eq!(
            normalize_url("ftp://example.com"),
            Err(InputError::UnsupportedScheme)
        );
    }
    #[test]
    fn url_19_whitespace_before_scheme() {
        assert_eq!(
            normalize_url("not a url://foo"),
            Err(InputError::InvalidHost)
        );
    }
    #[test]
    fn url_20_userinfo() {
        assert_eq!(
            normalize_url("https://user@example.com"),
            Err(InputError::UserinfoNotAllowed)
        );
    }
    #[test]
    fn url_21_port() {
        assert_eq!(
            normalize_url("https://example.com:8080"),
            Err(InputError::PortNotAllowed)
        );
    }
    #[test]
    fn url_22_bare_colon() {
        assert_eq!(
            normalize_url("example.com:443"),
            Err(InputError::InvalidHost)
        );
    }
    #[test]
    fn url_23_non_ascii() {
        assert_eq!(
            normalize_url("https://exämple.com"),
            Err(InputError::NonAsciiHost)
        );
    }
    #[test]
    fn url_24_label_leading_hyphen() {
        assert_eq!(
            normalize_url("https://-example.com"),
            Err(InputError::InvalidHost)
        );
    }
    #[test]
    fn url_25_label_trailing_hyphen() {
        assert_eq!(
            normalize_url("https://example-.com"),
            Err(InputError::InvalidHost)
        );
    }
    #[test]
    fn url_26_underscore_in_label() {
        assert_eq!(
            normalize_url("https://exa_mple.com"),
            Err(InputError::InvalidHost)
        );
    }
    #[test]
    fn url_27_empty_leading_label() {
        assert_eq!(
            normalize_url("https://.example.com"),
            Err(InputError::InvalidHost)
        );
    }
    #[test]
    fn url_28_empty_middle_label() {
        assert_eq!(
            normalize_url("https://example..com"),
            Err(InputError::InvalidHost)
        );
    }
    #[test]
    fn url_29_single_label() {
        assert_eq!(normalize_url("localhost"), Err(InputError::InvalidHost));
    }
    #[test]
    fn url_30_backslash() {
        assert_eq!(
            normalize_url("https://example.com\\bad"),
            Err(InputError::InvalidHost)
        );
    }
    #[test]
    fn url_31_whitespace_in_path() {
        assert_eq!(
            normalize_url("https://example.com/path with spaces"),
            Err(InputError::InvalidHost)
        );
    }
    #[test]
    fn url_32_ipv4_literal() {
        assert_eq!(normalize_url("127.0.0.1"), Err(InputError::InvalidHost));
    }
    #[test]
    fn url_33_http_ipv4() {
        assert_eq!(
            normalize_url("http://127.0.0.1"),
            Err(InputError::InvalidHost)
        );
    }
    #[test]
    fn url_34_ipv6_literal() {
        assert_eq!(normalize_url("https://[::1]"), Err(InputError::InvalidHost));
    }

    #[test]
    fn url_35_label_too_long() {
        let host: String = (0..64).map(|_| 'a').collect();
        let input = alloc::format!("https://{}.com", host);
        assert_eq!(normalize_url(&input), Err(InputError::InvalidHost));
    }

    #[test]
    fn url_36_total_too_long() {
        let host: String = alloc::format!("{}a", "a.".repeat(127));
        let input = alloc::format!("https://{}", host);
        assert_eq!(normalize_url(&input), Err(InputError::InvalidHost));
    }

    #[test]
    fn url_del_in_authority() {
        assert_eq!(
            normalize_url("https://exa\x7Fmple.com"),
            Err(InputError::InvalidHost)
        );
    }

    #[test]
    fn url_del_in_path_suffix() {
        assert_eq!(
            normalize_url("https://example.com/foo\x7Fbar"),
            Err(InputError::InvalidHost)
        );
    }

    #[test]
    fn normalization_collisions() {
        for input in [
            "example.com",
            "www.example.com",
            "https://EXAMPLE.COM.",
            "http://www.EXAMPLE.com/path?q=1#f",
        ] {
            assert_eq!(normalize_url(input).unwrap(), "example.com");
        }
    }

    #[test]
    fn catalog_example_com_resolves() {
        let entry = lookup_profile("example.com").unwrap();
        assert_eq!(entry.company_name, "Example SaaS");
        assert_eq!(entry.industry, "B2B SaaS");
        assert_eq!(entry.segment, "SMB");
        assert_eq!(entry.audience, "creators");
    }

    #[test]
    fn catalog_factory_example_com_resolves() {
        let entry = lookup_profile("factory.example.com").unwrap();
        assert_eq!(entry.industry, "manufacturing");
        assert_eq!(entry.segment, "enterprise");
        assert_eq!(entry.audience, "B2B");
    }

    #[test]
    fn catalog_shop_example_com_resolves() {
        let entry = lookup_profile("shop.example.com").unwrap();
        assert_eq!(entry.industry, "retail");
        assert_eq!(entry.segment, "SMB");
        assert_eq!(entry.audience, "B2C");
    }

    #[test]
    fn catalog_unknown_returns_none() {
        assert!(lookup_profile("unknown.example.net").is_none());
        assert!(lookup_profile("acme-corp.com").is_none());
        assert!(lookup_profile("").is_none());
    }

    #[test]
    fn catalog_only_matches_normalized_form() {
        assert!(lookup_profile("WWW.example.com").is_none());
        assert!(lookup_profile("www.example.com").is_none());
        assert!(lookup_profile("Example.Com").is_none());
        assert!(lookup_profile("example.com.").is_none());
    }

    #[test]
    fn end_to_end_known_profile() {
        let normalized = normalize_url("HTTPS://Example.Com./some-path").unwrap();
        assert_eq!(normalized, "example.com");
        let entry = lookup_profile(&normalized).unwrap();
        assert_eq!(entry.industry, "B2B SaaS");
    }

    #[test]
    fn end_to_end_unknown_profile() {
        let normalized = normalize_url("https://unknown.example.net").unwrap();
        assert!(lookup_profile(&normalized).is_none());
    }

    #[test]
    fn end_to_end_all_catalog_hosts() {
        for input in [
            "https://example.com",
            "https://factory.example.com",
            "https://shop.example.com",
        ] {
            let normalized = normalize_url(input).unwrap();
            assert!(
                lookup_profile(&normalized).is_some(),
                "expected catalog hit for {}",
                input,
            );
        }
    }
}

// ═════════════════════════════════════════════════════════════════════════
// Section 3 -- Output encoding + WASM ABI exports
// ═════════════════════════════════════════════════════════════════════════

const MAX_ENVELOPE_BYTES: i32 = 65536;
const ABI_ALIGN: usize = 4;

#[cfg(target_arch = "wasm32")]
mod allocator {
    use core::alloc::{GlobalAlloc, Layout};
    use core::cell::UnsafeCell;

    const HEAP_SIZE: usize = 262144;

    pub struct BumpAllocator {
        heap: UnsafeCell<[u8; HEAP_SIZE]>,
        offset: UnsafeCell<usize>,
    }

    unsafe impl Sync for BumpAllocator {}

    impl BumpAllocator {
        pub const fn new() -> Self {
            Self {
                heap: UnsafeCell::new([0u8; HEAP_SIZE]),
                offset: UnsafeCell::new(0),
            }
        }
    }

    unsafe impl GlobalAlloc for BumpAllocator {
        unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
            let offset = unsafe { &mut *self.offset.get() };
            let heap_ptr = unsafe { (*self.heap.get()).as_mut_ptr() };
            let size = layout.size();
            let align = layout.align();

            let base_addr = heap_ptr as usize;
            let current_addr = match base_addr.checked_add(*offset) {
                Some(addr) => addr,
                None => return core::ptr::null_mut(),
            };
            let align_mask = align - 1;
            let aligned_addr = match current_addr.checked_add(align_mask) {
                Some(v) => v & !align_mask,
                None => return core::ptr::null_mut(),
            };
            let padding = aligned_addr - current_addr;

            let final_offset = match (*offset)
                .checked_add(padding)
                .and_then(|v| v.checked_add(size))
            {
                Some(v) if v <= HEAP_SIZE => v,
                _ => return core::ptr::null_mut(),
            };

            *offset = final_offset;
            heap_ptr.add(final_offset - size)
        }

        unsafe fn dealloc(&self, _ptr: *mut u8, _layout: Layout) {}
    }
}

#[cfg(target_arch = "wasm32")]
#[global_allocator]
static ALLOCATOR: allocator::BumpAllocator = allocator::BumpAllocator::new();

#[cfg(target_arch = "wasm32")]
#[panic_handler]
fn panic(_info: &core::panic::PanicInfo) -> ! {
    core::arch::wasm32::unreachable()
}

pub(crate) fn build_success(
    parsed: &ParsedInput,
    normalized_url: &str,
    entry_opt: Option<&CatalogEntry>,
) -> Vec<u8> {
    let mut buf = Vec::new();
    let mut enc = minicbor::Encoder::new(&mut buf);

    enc.map(2).unwrap();

    enc.str("output").unwrap();
    enc.map(8).unwrap();

    enc.str("audience").unwrap();
    match entry_opt {
        Some(e) => {
            enc.str(e.audience).unwrap();
        }
        None => {
            enc.null().unwrap();
        }
    }

    enc.str("company_name").unwrap();
    match entry_opt {
        Some(e) => {
            enc.str(e.company_name).unwrap();
        }
        None => {
            enc.null().unwrap();
        }
    }

    enc.str("company_url_normalized").unwrap();
    enc.str(normalized_url).unwrap();

    enc.str("industry").unwrap();
    match entry_opt {
        Some(e) => {
            enc.str(e.industry).unwrap();
        }
        None => {
            enc.null().unwrap();
        }
    }

    enc.str("notes").unwrap();
    match &parsed.notes {
        Some(s) => {
            enc.str(s).unwrap();
        }
        None => {
            enc.null().unwrap();
        }
    }

    enc.str("profile_found").unwrap();
    enc.bool(entry_opt.is_some()).unwrap();

    enc.str("segment").unwrap();
    match entry_opt {
        Some(e) => {
            enc.str(e.segment).unwrap();
        }
        None => {
            enc.null().unwrap();
        }
    }

    enc.str("target_icp").unwrap();
    enc.str(&parsed.target_icp).unwrap();

    enc.str("type").unwrap();
    enc.str("Success").unwrap();

    buf
}

pub(crate) fn build_failure_invalid_input(message: &str) -> Vec<u8> {
    let mut buf = Vec::new();
    let mut enc = minicbor::Encoder::new(&mut buf);

    enc.map(2).unwrap();

    enc.str("error").unwrap();
    enc.map(2).unwrap();
    enc.str("error_class").unwrap();
    enc.str("INVALID_INPUT").unwrap();
    enc.str("message").unwrap();
    enc.str(message).unwrap();

    enc.str("type").unwrap();
    enc.str("Failure").unwrap();

    buf
}

pub(crate) fn process_envelope(envelope: &[u8]) -> Vec<u8> {
    let parsed = match parse_input(envelope) {
        Ok(p) => p,
        Err(e) => return build_failure_invalid_input(&e.message()),
    };

    let normalized = match normalize_url(&parsed.company_url) {
        Ok(n) => n,
        Err(e) => return build_failure_invalid_input(&e.message()),
    };

    let entry_opt = lookup_profile(&normalized);

    build_success(&parsed, &normalized, entry_opt)
}

#[cfg(target_arch = "wasm32")]
use core::slice;

#[cfg(target_arch = "wasm32")]
fn write_result(result_cbor: &[u8]) -> i32 {
    let total_len_usize = match 4usize.checked_add(result_cbor.len()) {
        Some(v) => v,
        None => core::arch::wasm32::unreachable(),
    };
    let total_len = match i32::try_from(total_len_usize) {
        Ok(n) if n > 0 => n,
        _ => core::arch::wasm32::unreachable(),
    };

    let result_ptr = alloc(total_len);
    if result_ptr == 0 {
        core::arch::wasm32::unreachable();
    }

    let ptr = result_ptr as *mut u8;
    let len_bytes = (result_cbor.len() as u32).to_le_bytes();
    unsafe {
        core::ptr::copy_nonoverlapping(len_bytes.as_ptr(), ptr, 4);
        core::ptr::copy_nonoverlapping(result_cbor.as_ptr(), ptr.add(4), result_cbor.len());
    }

    result_ptr
}

#[cfg(target_arch = "wasm32")]
#[no_mangle]
pub extern "C" fn alloc(len: i32) -> i32 {
    if len <= 0 {
        return 0;
    }
    let layout = match core::alloc::Layout::from_size_align(len as usize, ABI_ALIGN) {
        Ok(l) => l,
        Err(_) => return 0,
    };
    let ptr = unsafe { alloc::alloc::alloc(layout) };
    if ptr.is_null() {
        0
    } else {
        ptr as i32
    }
}

#[cfg(target_arch = "wasm32")]
#[no_mangle]
pub extern "C" fn free(ptr: i32, len: i32) {
    if ptr == 0 || len <= 0 {
        return;
    }
    let layout = match core::alloc::Layout::from_size_align(len as usize, ABI_ALIGN) {
        Ok(l) => l,
        Err(_) => return,
    };
    unsafe { alloc::alloc::dealloc(ptr as *mut u8, layout) };
}

#[cfg(target_arch = "wasm32")]
#[no_mangle]
pub extern "C" fn invoke(envelope_ptr: i32, envelope_len: i32) -> i32 {
    if envelope_ptr == 0 || envelope_len <= 0 || envelope_len > MAX_ENVELOPE_BYTES {
        return write_result(&build_failure_invalid_input(
            "invalid envelope pointer or length",
        ));
    }
    let envelope =
        unsafe { slice::from_raw_parts(envelope_ptr as *const u8, envelope_len as usize) };
    let result_cbor = process_envelope(envelope);
    write_result(&result_cbor)
}

// ── Section 3 host-side tests ────────────────────────────────────────────

#[cfg(all(test, not(target_arch = "wasm32")))]
mod section3_tests {
    use super::*;
    use minicbor::Decoder;

    fn decode_type_and_output_or_error(
        result: &[u8],
    ) -> (String, Option<Vec<u8>>, Option<Vec<u8>>) {
        let mut dec = Decoder::new(result);
        let len = dec.map().unwrap().unwrap();
        let mut type_str: Option<String> = None;
        let mut output_bytes: Option<Vec<u8>> = None;
        let mut error_bytes: Option<Vec<u8>> = None;
        for _ in 0..len {
            let key = dec.str().unwrap().to_string();
            let start = dec.position();
            dec.skip().unwrap();
            let end = dec.position();
            match key.as_str() {
                "type" => {
                    let mut d = Decoder::new(&result[start..end]);
                    type_str = Some(d.str().unwrap().to_string());
                }
                "output" => {
                    output_bytes = Some(result[start..end].to_vec());
                }
                "error" => {
                    error_bytes = Some(result[start..end].to_vec());
                }
                _ => {}
            }
        }
        (type_str.unwrap(), output_bytes, error_bytes)
    }

    fn decode_output_field_as_str(output_cbor: &[u8], key: &str) -> Option<String> {
        let mut dec = Decoder::new(output_cbor);
        let len = dec.map().unwrap().unwrap();
        for _ in 0..len {
            let k = dec.str().unwrap().to_string();
            if k == key {
                return match dec.datatype().unwrap() {
                    minicbor::data::Type::Null => {
                        dec.skip().unwrap();
                        None
                    }
                    _ => Some(dec.str().unwrap().to_string()),
                };
            }
            dec.skip().unwrap();
        }
        panic!("field {} not found", key);
    }

    fn decode_output_bool(output_cbor: &[u8], key: &str) -> bool {
        let mut dec = Decoder::new(output_cbor);
        let len = dec.map().unwrap().unwrap();
        for _ in 0..len {
            let k = dec.str().unwrap().to_string();
            if k == key {
                return dec.bool().unwrap();
            }
            dec.skip().unwrap();
        }
        panic!("field {} not found", key);
    }

    fn parsed(url: &str, icp: &str, notes: Option<&str>) -> ParsedInput {
        ParsedInput {
            company_url: url.to_string(),
            target_icp: icp.to_string(),
            notes: notes.map(|s| s.to_string()),
        }
    }

    #[test]
    fn success_example_com_emits_known_profile_facts() {
        let p = parsed("https://example.com", "b2b saas smb creators", None);
        let entry = lookup_profile("example.com").unwrap();
        let result = build_success(&p, "example.com", Some(entry));
        let (type_str, output, _) = decode_type_and_output_or_error(&result);
        assert_eq!(type_str, "Success");
        let output = output.unwrap();
        assert_eq!(
            decode_output_field_as_str(&output, "audience"),
            Some("creators".to_string())
        );
        assert_eq!(
            decode_output_field_as_str(&output, "company_name"),
            Some("Example SaaS".to_string())
        );
        assert_eq!(
            decode_output_field_as_str(&output, "company_url_normalized"),
            Some("example.com".to_string())
        );
        assert_eq!(
            decode_output_field_as_str(&output, "industry"),
            Some("B2B SaaS".to_string())
        );
        assert_eq!(
            decode_output_field_as_str(&output, "segment"),
            Some("SMB".to_string())
        );
        assert_eq!(
            decode_output_field_as_str(&output, "target_icp"),
            Some("b2b saas smb creators".to_string())
        );
        assert_eq!(decode_output_field_as_str(&output, "notes"), None);
        assert!(decode_output_bool(&output, "profile_found"));
    }

    #[test]
    fn success_factory_example_com_emits_factory_facts() {
        let p = parsed("https://factory.example.com", "manufacturing", None);
        let entry = lookup_profile("factory.example.com").unwrap();
        let result = build_success(&p, "factory.example.com", Some(entry));
        let (_, output, _) = decode_type_and_output_or_error(&result);
        let output = output.unwrap();
        assert_eq!(
            decode_output_field_as_str(&output, "industry"),
            Some("manufacturing".to_string())
        );
        assert_eq!(
            decode_output_field_as_str(&output, "segment"),
            Some("enterprise".to_string())
        );
        assert_eq!(
            decode_output_field_as_str(&output, "audience"),
            Some("B2B".to_string())
        );
    }

    #[test]
    fn success_shop_example_com_emits_shop_facts() {
        let p = parsed("https://shop.example.com", "retail", None);
        let entry = lookup_profile("shop.example.com").unwrap();
        let result = build_success(&p, "shop.example.com", Some(entry));
        let (_, output, _) = decode_type_and_output_or_error(&result);
        let output = output.unwrap();
        assert_eq!(
            decode_output_field_as_str(&output, "industry"),
            Some("retail".to_string())
        );
    }

    #[test]
    fn success_unknown_profile_emits_four_nulls_and_profile_found_false() {
        let p = parsed("https://unknown.example.net", "b2b saas", None);
        let result = build_success(&p, "unknown.example.net", None);
        let (type_str, output, _) = decode_type_and_output_or_error(&result);
        assert_eq!(type_str, "Success");
        let output = output.unwrap();
        assert_eq!(decode_output_field_as_str(&output, "audience"), None);
        assert_eq!(decode_output_field_as_str(&output, "company_name"), None);
        assert_eq!(decode_output_field_as_str(&output, "industry"), None);
        assert_eq!(decode_output_field_as_str(&output, "segment"), None);
        assert_eq!(
            decode_output_field_as_str(&output, "company_url_normalized"),
            Some("unknown.example.net".to_string())
        );
        assert_eq!(
            decode_output_field_as_str(&output, "target_icp"),
            Some("b2b saas".to_string())
        );
        assert_eq!(decode_output_field_as_str(&output, "notes"), None);
        assert!(!decode_output_bool(&output, "profile_found"));
    }

    #[test]
    fn success_notes_passthrough_when_string() {
        let p = parsed(
            "https://example.com",
            "x",
            Some("Introduced by the sales team."),
        );
        let entry = lookup_profile("example.com").unwrap();
        let result = build_success(&p, "example.com", Some(entry));
        let (_, output, _) = decode_type_and_output_or_error(&result);
        let output = output.unwrap();
        assert_eq!(
            decode_output_field_as_str(&output, "notes"),
            Some("Introduced by the sales team.".to_string()),
        );
    }

    #[test]
    fn success_notes_null_when_absent() {
        let p = parsed("https://example.com", "x", None);
        let entry = lookup_profile("example.com").unwrap();
        let result = build_success(&p, "example.com", Some(entry));
        let (_, output, _) = decode_type_and_output_or_error(&result);
        let output = output.unwrap();
        assert_eq!(decode_output_field_as_str(&output, "notes"), None);
    }

    #[test]
    fn failure_shape_is_well_formed() {
        let result = build_failure_invalid_input("something went wrong");
        let (type_str, output, error) = decode_type_and_output_or_error(&result);
        assert_eq!(type_str, "Failure");
        assert!(output.is_none());
        let error = error.unwrap();
        let mut dec = Decoder::new(&error);
        let len = dec.map().unwrap().unwrap();
        assert_eq!(len, 2);
        let mut saw_class = false;
        let mut saw_message = false;
        for _ in 0..len {
            let k = dec.str().unwrap().to_string();
            let v = dec.str().unwrap().to_string();
            match k.as_str() {
                "error_class" => {
                    assert_eq!(v, "INVALID_INPUT");
                    saw_class = true;
                }
                "message" => {
                    assert_eq!(v, "something went wrong");
                    saw_message = true;
                }
                _ => panic!("unexpected key {}", k),
            }
        }
        assert!(saw_class && saw_message);
    }

    #[test]
    fn success_output_is_byte_deterministic() {
        let p = parsed(
            "https://example.com",
            "b2b saas smb creators",
            Some("alpha"),
        );
        let entry = lookup_profile("example.com").unwrap();
        let a = build_success(&p, "example.com", Some(entry));
        let b = build_success(&p, "example.com", Some(entry));
        assert_eq!(a, b);
    }

    #[test]
    fn failure_output_is_byte_deterministic() {
        let a = build_failure_invalid_input("company_url: empty");
        let b = build_failure_invalid_input("company_url: empty");
        assert_eq!(a, b);
    }

    #[test]
    fn success_key_order_is_alphabetical_in_output_map() {
        let p = parsed("https://example.com", "x", None);
        let entry = lookup_profile("example.com").unwrap();
        let result = build_success(&p, "example.com", Some(entry));
        let (_, output, _) = decode_type_and_output_or_error(&result);
        let output = output.unwrap();
        let mut dec = Decoder::new(&output);
        let len = dec.map().unwrap().unwrap();
        let mut keys = Vec::new();
        for _ in 0..len {
            keys.push(dec.str().unwrap().to_string());
            dec.skip().unwrap();
        }
        let sorted = {
            let mut s = keys.clone();
            s.sort();
            s
        };
        assert_eq!(keys, sorted);
    }

    #[test]
    fn success_boundary_contract_complete() {
        let p = parsed("https://example.com", "x", None);
        let entry = lookup_profile("example.com").unwrap();
        let result = build_success(&p, "example.com", Some(entry));

        let mut dec = Decoder::new(&result);
        let top_len = dec.map().unwrap().unwrap();
        assert_eq!(top_len, 2);
        let mut top_keys = Vec::new();
        for _ in 0..top_len {
            top_keys.push(dec.str().unwrap().to_string());
            dec.skip().unwrap();
        }
        assert_eq!(
            top_keys,
            alloc::vec!["output".to_string(), "type".to_string()]
        );
        assert!(!top_keys.contains(&"error".to_string()));

        let (_, output, error) = decode_type_and_output_or_error(&result);
        assert!(error.is_none());
        let output = output.unwrap();

        let mut dec = Decoder::new(&output);
        let out_len = dec.map().unwrap().unwrap();
        assert_eq!(out_len, 8);
        let expected = alloc::vec![
            "audience".to_string(),
            "company_name".to_string(),
            "company_url_normalized".to_string(),
            "industry".to_string(),
            "notes".to_string(),
            "profile_found".to_string(),
            "segment".to_string(),
            "target_icp".to_string(),
        ];
        let mut actual = Vec::new();
        for _ in 0..out_len {
            actual.push(dec.str().unwrap().to_string());
            dec.skip().unwrap();
        }
        assert_eq!(actual, expected);

        assert!(decode_output_bool(&output, "profile_found"));
        assert_eq!(
            decode_output_field_as_str(&output, "company_url_normalized"),
            Some("example.com".to_string()),
        );
        assert_eq!(
            decode_output_field_as_str(&output, "target_icp"),
            Some("x".to_string()),
        );
    }

    #[test]
    fn failure_boundary_contract_complete() {
        let result = build_failure_invalid_input("test message");

        let mut dec = Decoder::new(&result);
        let top_len = dec.map().unwrap().unwrap();
        assert_eq!(top_len, 2);
        let mut top_keys = Vec::new();
        for _ in 0..top_len {
            top_keys.push(dec.str().unwrap().to_string());
            dec.skip().unwrap();
        }
        assert_eq!(
            top_keys,
            alloc::vec!["error".to_string(), "type".to_string()]
        );
        assert!(!top_keys.contains(&"output".to_string()));

        let (type_str, output, error) = decode_type_and_output_or_error(&result);
        assert_eq!(type_str, "Failure");
        assert!(output.is_none());
        let error = error.unwrap();

        let mut dec = Decoder::new(&error);
        let err_len = dec.map().unwrap().unwrap();
        assert_eq!(err_len, 2);
        let mut err_keys = Vec::new();
        for _ in 0..err_len {
            err_keys.push(dec.str().unwrap().to_string());
            dec.skip().unwrap();
        }
        assert_eq!(
            err_keys,
            alloc::vec!["error_class".to_string(), "message".to_string()]
        );
    }

    #[test]
    fn process_envelope_malformed_cbor_yields_failure() {
        let result = process_envelope(&[0xFF]);
        let (type_str, _, _) = decode_type_and_output_or_error(&result);
        assert_eq!(type_str, "Failure");
    }

    #[test]
    fn process_envelope_bad_url_yields_failure_not_success() {
        let bytes = {
            let mut buf = Vec::new();
            let mut enc = minicbor::Encoder::new(&mut buf);
            enc.map(1).unwrap();
            enc.str("input").unwrap();
            enc.map(2).unwrap();
            enc.str("company_url").unwrap();
            enc.str("ftp://example.com").unwrap();
            enc.str("target_icp").unwrap();
            enc.str("x").unwrap();
            buf
        };
        let result = process_envelope(&bytes);
        let (type_str, output, error) = decode_type_and_output_or_error(&result);
        assert_eq!(type_str, "Failure");
        assert!(output.is_none());
        assert!(error.is_some());
    }

    #[test]
    fn process_envelope_valid_input_yields_success_with_known_profile() {
        let bytes = {
            let mut buf = Vec::new();
            let mut enc = minicbor::Encoder::new(&mut buf);
            enc.map(1).unwrap();
            enc.str("input").unwrap();
            enc.map(2).unwrap();
            enc.str("company_url").unwrap();
            enc.str("https://example.com").unwrap();
            enc.str("target_icp").unwrap();
            enc.str("b2b saas smb creators").unwrap();
            buf
        };
        let result = process_envelope(&bytes);
        let (type_str, output, _) = decode_type_and_output_or_error(&result);
        assert_eq!(type_str, "Success");
        let output = output.unwrap();
        assert!(decode_output_bool(&output, "profile_found"));
        assert_eq!(
            decode_output_field_as_str(&output, "industry"),
            Some("B2B SaaS".to_string()),
        );
    }

    #[test]
    fn process_envelope_unknown_profile_yields_success_with_nulls() {
        let bytes = {
            let mut buf = Vec::new();
            let mut enc = minicbor::Encoder::new(&mut buf);
            enc.map(1).unwrap();
            enc.str("input").unwrap();
            enc.map(2).unwrap();
            enc.str("company_url").unwrap();
            enc.str("https://unknown.example.net").unwrap();
            enc.str("target_icp").unwrap();
            enc.str("b2b saas").unwrap();
            buf
        };
        let result = process_envelope(&bytes);
        let (type_str, output, _) = decode_type_and_output_or_error(&result);
        assert_eq!(type_str, "Success");
        let output = output.unwrap();
        assert!(!decode_output_bool(&output, "profile_found"));
        assert_eq!(decode_output_field_as_str(&output, "industry"), None);
        assert_eq!(decode_output_field_as_str(&output, "company_name"), None);
    }

    #[test]
    fn cross_field_invariant_known_profile_all_nonempty() {
        let p = parsed("https://example.com", "x", None);
        let entry = lookup_profile("example.com").unwrap();
        let result = build_success(&p, "example.com", Some(entry));
        let (_, output, _) = decode_type_and_output_or_error(&result);
        let output = output.unwrap();
        for field in ["company_name", "industry", "segment", "audience"] {
            let v = decode_output_field_as_str(&output, field);
            assert!(
                v.is_some() && !v.as_ref().unwrap().is_empty(),
                "known profile: {} must be nonempty string",
                field,
            );
        }
    }

    #[test]
    fn cross_field_invariant_unknown_profile_all_null() {
        let p = parsed("https://unknown.example.net", "x", None);
        let result = build_success(&p, "unknown.example.net", None);
        let (_, output, _) = decode_type_and_output_or_error(&result);
        let output = output.unwrap();
        for field in ["company_name", "industry", "segment", "audience"] {
            assert_eq!(
                decode_output_field_as_str(&output, field),
                None,
                "unknown profile: {} must be null",
                field,
            );
        }
    }
}
