// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

//! NCP icp-qualifier Brick (v0.1) -- rule-based ICP qualification against a
//! closed supported vocabulary (9 descriptors across industry, segment, and
//! audience) for the lead-qualification example graph (issue #29).
//!
//! Receives the 7-field edge-mapped projection of company-profile-lookup's
//! output and returns Success with next_action in {qualified, not_qualified,
//! needs_enrichment}. Invalid input returns Failure(INVALID_INPUT).
//!
//! Code is organized into three sections:
//! - Section 1: CBOR envelope + application-layer parsing + error types
//! - Section 2: ICP scoring + outcome selection                   (TBD, Phase B)
//! - Section 3: Output encoding + Model B ABI exports             (TBD, Phase C)
//!
//! Determinism: same input bytes produce byte-identical result bytes. Fully
//! offline: no DNS, no HTTP, no clock, no PRNG. Mirrors brick 1's envelope
//! parsing shape; strengthens it with application-layer trailing-bytes
//! rejection (safeguard 3), per-field duplicate-key variants, and exact
//! value-slice consumption for every per-field decoder.

#![cfg_attr(target_arch = "wasm32", no_std)]
#![cfg_attr(not(target_arch = "wasm32"), allow(dead_code))]

extern crate alloc;

use alloc::string::{String, ToString};
use alloc::vec::Vec;
use minicbor::data::Type;

// =========================================================================
// Section 1 -- CBOR parsing + application validation + error types
// =========================================================================
//
// Deterministic error precedence (position-invariant across CBOR key order
// for all errors on fully-decoded maps; decoder-induced MalformedCbor
// surfaces wherever minicbor establishes it first):
//
//   1. Envelope-layer structural (fail-fast on the outer map):
//        MalformedCbor, IndefiniteLengthMap, InputNotMap, TrailingBytes,
//        DuplicateInputField, MissingInputField.
//   2. Application-layer structural (evaluated after a complete
//      definite-length scan of the application map):
//        TooManyApplicationFields (bounded-work guard),
//        TrailingBytesApplication (safeguard 3 extension beyond brick 1).
//   3. UnknownField (any extra key beats any duplicate).
//   4. Duplicate* in schema-alphabetical order
//      (audience, company_name, industry, notes, profile_found, segment,
//       target_icp).
//   5. Missing* in schema-alphabetical order (same order).
//   6. Per-field type and length in schema-alphabetical order; within a
//      field, type-check (including IndefiniteLengthString) precedes
//      length-check. Each per-field decoder also enforces exact slice
//      consumption, surfacing MalformedCbor if the strict decoder and the
//      Pass 1 skip() disagree on value byte length.
//   7. Conditional invariant (FactsMustBe{Nonempty,Null}*) -- only
//      reachable when 1-6 are clean.
//
// All non-decoder-induced errors are determined by a full map walk BEFORE
// any result is returned; wire-order never silently decides a semantic
// error. The precedence guarantee covers fully-decoded application maps
// with semantic violations plus whatever malformed-input priority
// minicbor itself establishes.

// ---- Error type and messages -------------------------------------------

/// Internal parsing + validation error. All variants map to
/// `Failure(INVALID_INPUT)` at the Brick-output level (Section 3). Variant
/// distinction supports deterministic-precedence tests. All variants are
/// flat and zero-payload per the parser design (approved D2).
#[derive(Debug, Clone, PartialEq)]
pub(crate) enum InputError {
    // Envelope-layer
    MalformedCbor,
    TrailingBytes,
    IndefiniteLengthMap,
    MissingInputField,
    DuplicateInputField,
    InputNotMap,

    // Application-layer structural
    TooManyApplicationFields,
    TrailingBytesApplication,
    UnknownField,

    // Application-layer duplicate (schema-alphabetical precedence on
    // multiple duplicates)
    DuplicateAudience,
    DuplicateCompanyName,
    DuplicateIndustry,
    DuplicateNotes,
    DuplicateProfileFound,
    DuplicateSegment,
    DuplicateTargetIcp,

    // Application-layer missing (schema-alphabetical precedence on
    // multiple missing fields)
    MissingAudience,
    MissingCompanyName,
    MissingIndustry,
    MissingNotes,
    MissingProfileFound,
    MissingSegment,
    MissingTargetIcp,

    // Per-field type
    AudienceNotStringOrNull,
    CompanyNameNotStringOrNull,
    IndustryNotStringOrNull,
    NotesNotStringOrNull,
    ProfileFoundNotBool,
    SegmentNotStringOrNull,
    TargetIcpNotString,

    // CBOR string structure (indefinite-length strings are forbidden in
    // every string-typed field).
    IndefiniteLengthString,

    // Per-field length (Unicode code-point length via chars().count())
    AudienceTooLong,
    CompanyNameTooLong,
    IndustryTooLong,
    NotesTooLong,
    SegmentTooLong,
    TargetIcpEmpty,
    TargetIcpTooLong,

    // Conditional invariant (Pass 3; only reachable when 1-6 are clean)
    FactsMustBeNonemptyWhenFound,
    FactsMustBeNullWhenNotFound,
}

impl InputError {
    pub(crate) fn message(&self) -> String {
        match self {
            // Envelope-layer
            InputError::MalformedCbor => "envelope: malformed CBOR".to_string(),
            InputError::TrailingBytes => "envelope: trailing bytes after outer map".to_string(),
            InputError::IndefiniteLengthMap => {
                "input: indefinite-length maps not supported".to_string()
            }
            InputError::MissingInputField => "envelope: missing input field".to_string(),
            InputError::DuplicateInputField => "envelope: duplicate input field".to_string(),
            InputError::InputNotMap => "envelope: input must be a CBOR map".to_string(),

            // Application-layer structural
            InputError::TooManyApplicationFields => {
                "input: too many application fields (max 32)".to_string()
            }
            InputError::TrailingBytesApplication => {
                "input: trailing bytes after application map".to_string()
            }
            InputError::UnknownField => "input: unknown application field".to_string(),

            // Duplicate (schema-alphabetical)
            InputError::DuplicateAudience => "input: duplicate field 'audience'".to_string(),
            InputError::DuplicateCompanyName => "input: duplicate field 'company_name'".to_string(),
            InputError::DuplicateIndustry => "input: duplicate field 'industry'".to_string(),
            InputError::DuplicateNotes => "input: duplicate field 'notes'".to_string(),
            InputError::DuplicateProfileFound => {
                "input: duplicate field 'profile_found'".to_string()
            }
            InputError::DuplicateSegment => "input: duplicate field 'segment'".to_string(),
            InputError::DuplicateTargetIcp => "input: duplicate field 'target_icp'".to_string(),

            // Missing (schema-alphabetical)
            InputError::MissingAudience => "input: missing required field audience".to_string(),
            InputError::MissingCompanyName => {
                "input: missing required field company_name".to_string()
            }
            InputError::MissingIndustry => "input: missing required field industry".to_string(),
            InputError::MissingNotes => "input: missing required field notes".to_string(),
            InputError::MissingProfileFound => {
                "input: missing required field profile_found".to_string()
            }
            InputError::MissingSegment => "input: missing required field segment".to_string(),
            InputError::MissingTargetIcp => "input: missing required field target_icp".to_string(),

            // Type
            InputError::AudienceNotStringOrNull => {
                "audience: must be a CBOR text string or null".to_string()
            }
            InputError::CompanyNameNotStringOrNull => {
                "company_name: must be a CBOR text string or null".to_string()
            }
            InputError::IndustryNotStringOrNull => {
                "industry: must be a CBOR text string or null".to_string()
            }
            InputError::NotesNotStringOrNull => {
                "notes: must be a CBOR text string or null".to_string()
            }
            InputError::ProfileFoundNotBool => "profile_found: must be a CBOR boolean".to_string(),
            InputError::SegmentNotStringOrNull => {
                "segment: must be a CBOR text string or null".to_string()
            }
            InputError::TargetIcpNotString => "target_icp: must be a CBOR text string".to_string(),

            // String structure
            InputError::IndefiniteLengthString => {
                "input: indefinite-length text strings not supported".to_string()
            }

            // Length
            InputError::AudienceTooLong => "audience: exceeds 32 characters".to_string(),
            InputError::CompanyNameTooLong => "company_name: exceeds 256 characters".to_string(),
            InputError::IndustryTooLong => "industry: exceeds 64 characters".to_string(),
            InputError::NotesTooLong => "notes: exceeds 1024 characters".to_string(),
            InputError::SegmentTooLong => "segment: exceeds 32 characters".to_string(),
            InputError::TargetIcpEmpty => "target_icp: empty".to_string(),
            InputError::TargetIcpTooLong => "target_icp: exceeds 1024 characters".to_string(),

            // Conditional invariant
            InputError::FactsMustBeNonemptyWhenFound => {
                "input: when profile_found is true, company_name, industry, segment, \
                 and audience must be nonempty strings"
                    .to_string()
            }
            InputError::FactsMustBeNullWhenNotFound => {
                "input: when profile_found is false, company_name, industry, segment, \
                 and audience must be null"
                    .to_string()
            }
        }
    }
}

// ---- Validated input + constants ---------------------------------------

/// Validated application input returned by `parse_input` on success.
///
/// Lifetime is tied to the envelope bytes slice; all string fields borrow
/// into that slice without allocation.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct ParsedInput<'a> {
    pub(crate) company_name: Option<&'a str>,
    pub(crate) industry: Option<&'a str>,
    pub(crate) segment: Option<&'a str>,
    pub(crate) audience: Option<&'a str>,
    pub(crate) profile_found: bool,
    pub(crate) target_icp: &'a str,
    pub(crate) notes: Option<&'a str>,
}

pub(crate) const MAX_COMPANY_NAME_LEN: usize = 256;
pub(crate) const MAX_INDUSTRY_LEN: usize = 64;
pub(crate) const MAX_SEGMENT_LEN: usize = 32;
pub(crate) const MAX_AUDIENCE_LEN: usize = 32;
pub(crate) const MAX_TARGET_ICP_LEN: usize = 1024;
pub(crate) const MAX_NOTES_LEN: usize = 1024;
pub(crate) const MAX_APPLICATION_FIELDS: u64 = 32;

// ---- Envelope-layer extraction -----------------------------------------

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

// ---- Per-field decoders (borrowed, exact-slice consumption) ------------
//
// Safeguard 3: every per-field decoder re-decodes a `[start..end]` slice
// recorded by Pass 1 and asserts `decoder.position() == bytes.len()`
// after decoding. If the strict decoder consumes fewer bytes than Pass 1's
// `skip()` consumed, that is a MalformedCbor error -- a strict guard
// against malformed values whose header advertises a different length
// than their body.

fn decode_text_exact(bytes: &[u8], type_err: InputError) -> Result<&str, InputError> {
    let mut decoder = minicbor::Decoder::new(bytes);
    match decoder.datatype() {
        Ok(Type::String) => {
            let s = decoder.str().map_err(|_| InputError::MalformedCbor)?;
            if decoder.position() != bytes.len() {
                return Err(InputError::MalformedCbor);
            }
            Ok(s)
        }
        Ok(Type::StringIndef) => Err(InputError::IndefiniteLengthString),
        Ok(_) => Err(type_err),
        Err(_) => Err(InputError::MalformedCbor),
    }
}

fn decode_optional_text_exact(
    bytes: &[u8],
    type_err: InputError,
) -> Result<Option<&str>, InputError> {
    let mut decoder = minicbor::Decoder::new(bytes);
    match decoder.datatype() {
        Ok(Type::Null) => {
            decoder.skip().map_err(|_| InputError::MalformedCbor)?;
            if decoder.position() != bytes.len() {
                return Err(InputError::MalformedCbor);
            }
            Ok(None)
        }
        Ok(Type::String) => {
            let s = decoder.str().map_err(|_| InputError::MalformedCbor)?;
            if decoder.position() != bytes.len() {
                return Err(InputError::MalformedCbor);
            }
            Ok(Some(s))
        }
        Ok(Type::StringIndef) => Err(InputError::IndefiniteLengthString),
        Ok(_) => Err(type_err),
        Err(_) => Err(InputError::MalformedCbor),
    }
}

fn decode_bool_exact(bytes: &[u8]) -> Result<bool, InputError> {
    let mut decoder = minicbor::Decoder::new(bytes);
    match decoder.datatype() {
        Ok(Type::Bool) => {
            let b = decoder.bool().map_err(|_| InputError::MalformedCbor)?;
            if decoder.position() != bytes.len() {
                return Err(InputError::MalformedCbor);
            }
            Ok(b)
        }
        Ok(_) => Err(InputError::ProfileFoundNotBool),
        Err(_) => Err(InputError::MalformedCbor),
    }
}

// ---- Application-layer scan + parse ------------------------------------

/// Positions recorded during Pass 1 for each known field. `(start, end)`
/// bytes into the application input slice.
struct ApplicationScan {
    audience: (usize, usize),
    company_name: (usize, usize),
    industry: (usize, usize),
    notes: (usize, usize),
    profile_found: (usize, usize),
    segment: (usize, usize),
    target_icp: (usize, usize),
}

/// Pass 1: walk the entire application map, record field positions,
/// surface position-invariant structural errors in the agreed precedence.
fn scan_application_map(input_bytes: &[u8]) -> Result<ApplicationScan, InputError> {
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

    // Pass 1 state. `Option<(usize, usize)>` for position: `None` means
    // "field absent from map"; `Some` means "field present with value at
    // this slice". A separate `dup_*` boolean captures the independent
    // meaning of "field appeared more than once". The two Options never
    // alias (safeguard 2).
    let mut pos_audience: Option<(usize, usize)> = None;
    let mut pos_company_name: Option<(usize, usize)> = None;
    let mut pos_industry: Option<(usize, usize)> = None;
    let mut pos_notes: Option<(usize, usize)> = None;
    let mut pos_profile_found: Option<(usize, usize)> = None;
    let mut pos_segment: Option<(usize, usize)> = None;
    let mut pos_target_icp: Option<(usize, usize)> = None;

    let mut had_unknown = false;
    let mut dup_audience = false;
    let mut dup_company_name = false;
    let mut dup_industry = false;
    let mut dup_notes = false;
    let mut dup_profile_found = false;
    let mut dup_segment = false;
    let mut dup_target_icp = false;

    for _ in 0..len {
        if decoder.datatype().map_err(|_| InputError::MalformedCbor)? != Type::String {
            return Err(InputError::MalformedCbor);
        }
        let key = decoder.str().map_err(|_| InputError::MalformedCbor)?;
        let start = decoder.position();
        decoder.skip().map_err(|_| InputError::MalformedCbor)?;
        let end = decoder.position();

        match key {
            "audience" => {
                if pos_audience.is_some() {
                    dup_audience = true;
                } else {
                    pos_audience = Some((start, end));
                }
            }
            "company_name" => {
                if pos_company_name.is_some() {
                    dup_company_name = true;
                } else {
                    pos_company_name = Some((start, end));
                }
            }
            "industry" => {
                if pos_industry.is_some() {
                    dup_industry = true;
                } else {
                    pos_industry = Some((start, end));
                }
            }
            "notes" => {
                if pos_notes.is_some() {
                    dup_notes = true;
                } else {
                    pos_notes = Some((start, end));
                }
            }
            "profile_found" => {
                if pos_profile_found.is_some() {
                    dup_profile_found = true;
                } else {
                    pos_profile_found = Some((start, end));
                }
            }
            "segment" => {
                if pos_segment.is_some() {
                    dup_segment = true;
                } else {
                    pos_segment = Some((start, end));
                }
            }
            "target_icp" => {
                if pos_target_icp.is_some() {
                    dup_target_icp = true;
                } else {
                    pos_target_icp = Some((start, end));
                }
            }
            _ => {
                had_unknown = true;
            }
        }
    }

    // Safeguard 3 extension: application-layer trailing-bytes rejection.
    if decoder.position() < input_bytes.len() {
        return Err(InputError::TrailingBytesApplication);
    }

    // Precedence: UnknownField beats any duplicate.
    if had_unknown {
        return Err(InputError::UnknownField);
    }

    // Precedence: duplicate in schema-alphabetical order.
    if dup_audience {
        return Err(InputError::DuplicateAudience);
    }
    if dup_company_name {
        return Err(InputError::DuplicateCompanyName);
    }
    if dup_industry {
        return Err(InputError::DuplicateIndustry);
    }
    if dup_notes {
        return Err(InputError::DuplicateNotes);
    }
    if dup_profile_found {
        return Err(InputError::DuplicateProfileFound);
    }
    if dup_segment {
        return Err(InputError::DuplicateSegment);
    }
    if dup_target_icp {
        return Err(InputError::DuplicateTargetIcp);
    }

    // Precedence: missing in schema-alphabetical order.
    let audience = pos_audience.ok_or(InputError::MissingAudience)?;
    let company_name = pos_company_name.ok_or(InputError::MissingCompanyName)?;
    let industry = pos_industry.ok_or(InputError::MissingIndustry)?;
    let notes = pos_notes.ok_or(InputError::MissingNotes)?;
    let profile_found = pos_profile_found.ok_or(InputError::MissingProfileFound)?;
    let segment = pos_segment.ok_or(InputError::MissingSegment)?;
    let target_icp = pos_target_icp.ok_or(InputError::MissingTargetIcp)?;

    Ok(ApplicationScan {
        audience,
        company_name,
        industry,
        notes,
        profile_found,
        segment,
        target_icp,
    })
}

/// Pass 2 + Pass 3: validate per-field type/length in schema-alphabetical
/// order, then enforce the profile_found conditional invariant.
fn parse_application(input_bytes: &[u8]) -> Result<ParsedInput<'_>, InputError> {
    let scan = scan_application_map(input_bytes)?;

    // audience (type -> length)
    let audience = decode_optional_text_exact(
        &input_bytes[scan.audience.0..scan.audience.1],
        InputError::AudienceNotStringOrNull,
    )?;
    if let Some(s) = audience {
        if s.chars().count() > MAX_AUDIENCE_LEN {
            return Err(InputError::AudienceTooLong);
        }
    }

    // company_name (type -> length)
    let company_name = decode_optional_text_exact(
        &input_bytes[scan.company_name.0..scan.company_name.1],
        InputError::CompanyNameNotStringOrNull,
    )?;
    if let Some(s) = company_name {
        if s.chars().count() > MAX_COMPANY_NAME_LEN {
            return Err(InputError::CompanyNameTooLong);
        }
    }

    // industry (type -> length)
    let industry = decode_optional_text_exact(
        &input_bytes[scan.industry.0..scan.industry.1],
        InputError::IndustryNotStringOrNull,
    )?;
    if let Some(s) = industry {
        if s.chars().count() > MAX_INDUSTRY_LEN {
            return Err(InputError::IndustryTooLong);
        }
    }

    // notes (type -> length)
    let notes = decode_optional_text_exact(
        &input_bytes[scan.notes.0..scan.notes.1],
        InputError::NotesNotStringOrNull,
    )?;
    if let Some(s) = notes {
        if s.chars().count() > MAX_NOTES_LEN {
            return Err(InputError::NotesTooLong);
        }
    }

    // profile_found (type only; bool has no length)
    let profile_found =
        decode_bool_exact(&input_bytes[scan.profile_found.0..scan.profile_found.1])?;

    // segment (type -> length)
    let segment = decode_optional_text_exact(
        &input_bytes[scan.segment.0..scan.segment.1],
        InputError::SegmentNotStringOrNull,
    )?;
    if let Some(s) = segment {
        if s.chars().count() > MAX_SEGMENT_LEN {
            return Err(InputError::SegmentTooLong);
        }
    }

    // target_icp (type -> empty -> length)
    let target_icp = decode_text_exact(
        &input_bytes[scan.target_icp.0..scan.target_icp.1],
        InputError::TargetIcpNotString,
    )?;
    if target_icp.is_empty() {
        return Err(InputError::TargetIcpEmpty);
    }
    if target_icp.chars().count() > MAX_TARGET_ICP_LEN {
        return Err(InputError::TargetIcpTooLong);
    }

    // Pass 3: conditional invariant on profile_found.
    //
    // profile_found == true:  all 4 facts must be Some(nonempty).
    //                         Some("") is caught here, not in Pass 2,
    //                         because the base schema permits empty
    //                         strings; only the if/then branch adds
    //                         minLength: 1.
    // profile_found == false: all 4 facts must be None (CBOR null).
    if profile_found {
        let any_null_or_empty = audience.map(|s| s.is_empty()).unwrap_or(true)
            || company_name.map(|s| s.is_empty()).unwrap_or(true)
            || industry.map(|s| s.is_empty()).unwrap_or(true)
            || segment.map(|s| s.is_empty()).unwrap_or(true);
        if any_null_or_empty {
            return Err(InputError::FactsMustBeNonemptyWhenFound);
        }
    } else if audience.is_some()
        || company_name.is_some()
        || industry.is_some()
        || segment.is_some()
    {
        return Err(InputError::FactsMustBeNullWhenNotFound);
    }

    Ok(ParsedInput {
        company_name,
        industry,
        segment,
        audience,
        profile_found,
        target_icp,
        notes,
    })
}

/// Public parser entry point. Accepts envelope bytes, extracts the
/// `envelope.input` slice, and validates the application-layer input.
pub(crate) fn parse_input(envelope: &[u8]) -> Result<ParsedInput<'_>, InputError> {
    let input_bytes = extract_input_bytes(envelope)?;
    parse_application(input_bytes)
}

// =========================================================================
// Section 2 -- ICP scoring + outcome selection
// =========================================================================
//
// Deterministic rule-based qualification against a closed 9-descriptor
// vocabulary (3 descriptors per dimension). All matching is:
//
//   - case-insensitive (both target_icp and profile dim values are
//     ASCII-lowercased before comparison);
//   - whitespace-tokenized on literal ASCII whitespace boundaries;
//     punctuation stays attached to tokens (unrecognized tokens are
//     ignored; only the closed vocabulary is matched);
//   - presence-based (set-membership on target_icp tokens, with no
//     exclusivity across dimensions -- the "b2b" token legitimately
//     counts toward both industry via "b2b saas" and audience via
//     standalone "b2b" if both appear);
//   - position-invariant (reversing ICP token order never changes
//     score, outcome, or reason bytes).
//
// Multi-word descriptors:
//   - "b2b saas" is split by whitespace into ["b2b", "saas"] and
//     requires that pair to appear contiguously in the token list.
//   - "mid-market" contains no whitespace, so it is a single
//     hyphenated token; it matches when the token "mid-market"
//     appears. "mid market" (two tokens) does NOT match.
//
// Decision rule (integer comparison, no FP boundary):
//   matched_dimensions = industry_match + segment_match + audience_match
//                        (integer in 0..=3)
//   score  = matched_dimensions as f64 / 3.0  (0.0, 1/3, 2/3, or 1.0)
//   >= 2  => qualified
//   <= 1  => not_qualified
//
// needs_enrichment short-circuits:
//   - profile_found == false:  "Company domain not in catalog"
//   - profile_found == true AND any dimension has zero recognized
//     descriptors from its vocab appearing in target_icp:
//     "Target ICP contains no supported terms in <uncovered dims,
//      canonical order>"
//
// Known profile + fully-interpretable ICP always produces qualified
// or not_qualified -- including when score is zero. needs_enrichment
// never masks a successful but zero-matching qualification.

/// Vocabulary tables in CANONICAL order. Both the per-dimension
/// "recognized" list and the "target recognizes <...>" portion of
/// reason strings iterate these arrays and preserve this order,
/// independent of target_icp token order.
pub(crate) const INDUSTRY_DESCRIPTORS: &[&str] = &["b2b saas", "manufacturing", "retail"];
pub(crate) const SEGMENT_DESCRIPTORS: &[&str] = &["smb", "mid-market", "enterprise"];
pub(crate) const AUDIENCE_DESCRIPTORS: &[&str] = &["b2b", "b2c", "creators"];

#[derive(Debug, Clone, Copy, PartialEq)]
pub(crate) enum NextAction {
    Qualified,
    NotQualified,
    NeedsEnrichment,
}

impl NextAction {
    pub(crate) fn as_str(&self) -> &'static str {
        match self {
            NextAction::Qualified => "qualified",
            NextAction::NotQualified => "not_qualified",
            NextAction::NeedsEnrichment => "needs_enrichment",
        }
    }
}

/// Output of `qualify`. Pass-through fields `company_name` and
/// `industry` are None iff `profile_found` was false in the input.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct ScoreOutcome {
    pub(crate) company_name: Option<String>,
    pub(crate) industry: Option<String>,
    pub(crate) icp_match: Option<bool>,
    pub(crate) score: Option<f64>,
    pub(crate) reasons: Vec<String>,
    pub(crate) next_action: NextAction,
}

/// Deterministic rule-based qualification. The caller must have
/// validated `parsed` via `parse_input`; `qualify` relies on Pass 3's
/// invariant that `profile_found=true` implies all four facts are
/// `Some(nonempty)`.
pub(crate) fn qualify(parsed: &ParsedInput) -> ScoreOutcome {
    // Short-circuit: profile not found.
    if !parsed.profile_found {
        return ScoreOutcome {
            company_name: None,
            industry: None,
            icp_match: None,
            score: None,
            reasons: alloc::vec![String::from("Company domain not in catalog")],
            next_action: NextAction::NeedsEnrichment,
        };
    }

    // Pass 3 invariant: all four facts Some(nonempty).
    let company_name_in = parsed.company_name.unwrap();
    let industry_in = parsed.industry.unwrap();
    let segment_in = parsed.segment.unwrap();
    let audience_in = parsed.audience.unwrap();

    // Lowercase target_icp and tokenize on whitespace.
    let target_lower = parsed.target_icp.to_ascii_lowercase();
    let tokens: Vec<&str> = target_lower.split_ascii_whitespace().collect();

    // Per-dimension recognized descriptors in canonical order.
    let industry_recognized = find_recognized(&tokens, INDUSTRY_DESCRIPTORS);
    let segment_recognized = find_recognized(&tokens, SEGMENT_DESCRIPTORS);
    let audience_recognized = find_recognized(&tokens, AUDIENCE_DESCRIPTORS);

    // Short-circuit: ICP insufficient (any dim uninterpretable).
    let uncovered_industry = industry_recognized.is_empty();
    let uncovered_segment = segment_recognized.is_empty();
    let uncovered_audience = audience_recognized.is_empty();

    if uncovered_industry || uncovered_segment || uncovered_audience {
        let mut uncovered: Vec<&'static str> = Vec::new();
        if uncovered_industry {
            uncovered.push("industry");
        }
        if uncovered_segment {
            uncovered.push("segment");
        }
        if uncovered_audience {
            uncovered.push("audience");
        }
        let mut reason = String::from("Target ICP contains no supported terms in ");
        append_canonical_list(&mut reason, &uncovered);
        return ScoreOutcome {
            company_name: Some(company_name_in.to_string()),
            industry: Some(industry_in.to_string()),
            icp_match: None,
            score: None,
            reasons: alloc::vec![reason],
            next_action: NextAction::NeedsEnrichment,
        };
    }

    // All three dimensions interpretable. Compute per-dim match.
    let industry_lower = industry_in.to_ascii_lowercase();
    let segment_lower = segment_in.to_ascii_lowercase();
    let audience_lower = audience_in.to_ascii_lowercase();

    let industry_match = industry_recognized.iter().any(|&d| d == industry_lower);
    let segment_match = segment_recognized.iter().any(|&d| d == segment_lower);
    let audience_match = audience_recognized.iter().any(|&d| d == audience_lower);

    let matched_dimensions: u8 = industry_match as u8 + segment_match as u8 + audience_match as u8;
    let score = (matched_dimensions as f64) / 3.0;
    let qualified = matched_dimensions >= 2;
    let next_action = if qualified {
        NextAction::Qualified
    } else {
        NextAction::NotQualified
    };

    // Build reasons in canonical order: industry -> segment -> audience.
    let reasons: Vec<String> = alloc::vec![
        reason_for_dim(
            "Industry",
            &industry_lower,
            &industry_recognized,
            industry_match,
        ),
        reason_for_dim(
            "Segment",
            &segment_lower,
            &segment_recognized,
            segment_match,
        ),
        reason_for_dim(
            "Audience",
            &audience_lower,
            &audience_recognized,
            audience_match,
        ),
    ];

    ScoreOutcome {
        company_name: Some(company_name_in.to_string()),
        industry: Some(industry_in.to_string()),
        icp_match: Some(qualified),
        score: Some(score),
        reasons,
        next_action,
    }
}

/// Scan `tokens` for occurrences of any descriptor in `descriptors`
/// (canonical vocab order). Returns the descriptors found, in canonical
/// order, without duplicates. Multi-word descriptors match contiguously
/// (via `windows`); single-word descriptors match in any position.
fn find_recognized<'a>(tokens: &[&str], descriptors: &'a [&'a str]) -> Vec<&'a str> {
    let mut found: Vec<&'a str> = Vec::new();
    for &desc in descriptors {
        let desc_words: Vec<&str> = desc.split_ascii_whitespace().collect();
        let present = if desc_words.len() == 1 {
            tokens.iter().any(|&t| t == desc_words[0])
        } else {
            tokens
                .windows(desc_words.len())
                .any(|window| window == desc_words.as_slice())
        };
        if present {
            found.push(desc);
        }
    }
    found
}

/// Reason string for one dimension. Pure function, allocates only the
/// returned String.
///
/// Templates (approved amendment: multi-descriptor cases disclose all
/// recognized alternatives, in canonical order, under both match and
/// mismatch to prevent Strategy A ambiguity from being hidden):
///
///   Match + 1 descriptor recognized:
///     "<Dim> matches: <profile>"
///   Match + 2+ descriptors recognized:
///     "<Dim> matches: <profile> (target recognizes <list>)"
///   Mismatch (any 1+ recognized):
///     "<Dim> mismatch: profile=<profile>, target recognizes <list>"
fn reason_for_dim(
    dim_name: &str,
    profile_lower: &str,
    recognized: &[&str],
    is_match: bool,
) -> String {
    let mut s = String::with_capacity(96);
    s.push_str(dim_name);
    if is_match {
        s.push_str(" matches: ");
        s.push_str(profile_lower);
        if recognized.len() > 1 {
            s.push_str(" (target recognizes ");
            append_canonical_list(&mut s, recognized);
            s.push(')');
        }
    } else {
        s.push_str(" mismatch: profile=");
        s.push_str(profile_lower);
        s.push_str(", target recognizes ");
        append_canonical_list(&mut s, recognized);
    }
    s
}

/// Append items as a comma-separated list to `s`. Pure function.
fn append_canonical_list(s: &mut String, items: &[&str]) {
    for (i, &item) in items.iter().enumerate() {
        if i > 0 {
            s.push_str(", ");
        }
        s.push_str(item);
    }
}

// ---- Section 2 host-side unit tests ------------------------------------

#[cfg(all(test, not(target_arch = "wasm32")))]
mod section2_tests {
    use super::*;

    // ---- Test fixture helpers ------------------------------------------

    fn pi<'a>(
        profile_found: bool,
        company_name: Option<&'a str>,
        industry: Option<&'a str>,
        segment: Option<&'a str>,
        audience: Option<&'a str>,
        target_icp: &'a str,
    ) -> ParsedInput<'a> {
        ParsedInput {
            company_name,
            industry,
            segment,
            audience,
            profile_found,
            target_icp,
            notes: None,
        }
    }

    fn example_saas(target_icp: &str) -> ParsedInput<'_> {
        pi(
            true,
            Some("Example SaaS"),
            Some("B2B SaaS"),
            Some("SMB"),
            Some("creators"),
            target_icp,
        )
    }
    fn shop_example(target_icp: &str) -> ParsedInput<'_> {
        pi(
            true,
            Some("Shop Example"),
            Some("retail"),
            Some("SMB"),
            Some("B2C"),
            target_icp,
        )
    }
    fn factory_example(target_icp: &str) -> ParsedInput<'_> {
        pi(
            true,
            Some("Factory Example"),
            Some("manufacturing"),
            Some("enterprise"),
            Some("B2B"),
            target_icp,
        )
    }
    fn unknown(target_icp: &str) -> ParsedInput<'_> {
        pi(false, None, None, None, None, target_icp)
    }

    // ---- 11 specimen cases from design review --------------------------

    #[test]
    fn specimen_1_example_saas_full_match() {
        // target_icp "b2b saas smb creators":
        //   tokens: ["b2b", "saas", "smb", "creators"]
        //   industry recognized: ["b2b saas"] (contiguous match)
        //   segment  recognized: ["smb"]
        //   audience recognized: ["b2b", "creators"]  -- b2b standalone IS
        //                                                 an audience descriptor
        //                                                 (intentional overlap)
        //   profile=b2b saas / smb / creators => all three dims match.
        let out = qualify(&example_saas("b2b saas smb creators"));
        assert_eq!(out.next_action, NextAction::Qualified);
        assert_eq!(out.icp_match, Some(true));
        assert_eq!(out.score.unwrap().to_bits(), (3.0f64 / 3.0f64).to_bits());
        assert_eq!(out.company_name.as_deref(), Some("Example SaaS"));
        assert_eq!(out.industry.as_deref(), Some("B2B SaaS"));
        assert_eq!(out.reasons.len(), 3);
        assert_eq!(out.reasons[0], "Industry matches: b2b saas");
        assert_eq!(out.reasons[1], "Segment matches: smb");
        // Multi-descriptor match: audience has b2b AND creators recognized.
        assert_eq!(
            out.reasons[2],
            "Audience matches: creators (target recognizes b2b, creators)"
        );
    }

    #[test]
    fn specimen_2_example_saas_two_of_three() {
        let out = qualify(&example_saas("b2b saas smb b2b"));
        assert_eq!(out.next_action, NextAction::Qualified);
        assert_eq!(out.icp_match, Some(true));
        assert_eq!(out.score.unwrap().to_bits(), (2.0f64 / 3.0f64).to_bits());
        assert_eq!(out.reasons[0], "Industry matches: b2b saas");
        assert_eq!(out.reasons[1], "Segment matches: smb");
        assert_eq!(
            out.reasons[2],
            "Audience mismatch: profile=creators, target recognizes b2b"
        );
    }

    #[test]
    fn specimen_3_example_saas_one_of_three() {
        let out = qualify(&example_saas("manufacturing smb b2b"));
        assert_eq!(out.next_action, NextAction::NotQualified);
        assert_eq!(out.icp_match, Some(false));
        assert_eq!(out.score.unwrap().to_bits(), (1.0f64 / 3.0f64).to_bits());
        assert_eq!(
            out.reasons[0],
            "Industry mismatch: profile=b2b saas, target recognizes manufacturing"
        );
        assert_eq!(out.reasons[1], "Segment matches: smb");
        assert_eq!(
            out.reasons[2],
            "Audience mismatch: profile=creators, target recognizes b2b"
        );
    }

    #[test]
    fn specimen_4_example_saas_zero_match_is_not_qualified_not_needs_enrichment() {
        // Known profile + fully interpretable ICP + zero match ->
        // not_qualified. needs_enrichment is NOT the fallback here.
        let out = qualify(&example_saas("manufacturing enterprise b2c"));
        assert_eq!(out.next_action, NextAction::NotQualified);
        assert_eq!(out.icp_match, Some(false));
        assert_eq!(out.score.unwrap().to_bits(), (0.0f64 / 3.0f64).to_bits());
        assert_eq!(out.company_name.as_deref(), Some("Example SaaS"));
        assert_eq!(out.industry.as_deref(), Some("B2B SaaS"));
        assert_eq!(out.reasons.len(), 3);
    }

    #[test]
    fn specimen_5_example_saas_segment_uninterpretable() {
        let out = qualify(&example_saas("b2b saas creators"));
        assert_eq!(out.next_action, NextAction::NeedsEnrichment);
        assert_eq!(out.icp_match, None);
        assert_eq!(out.score, None);
        assert_eq!(out.reasons.len(), 1);
        assert_eq!(
            out.reasons[0],
            "Target ICP contains no supported terms in segment"
        );
        // Known profile => facts echo through on needs_enrichment-via-ICP.
        assert_eq!(out.company_name.as_deref(), Some("Example SaaS"));
        assert_eq!(out.industry.as_deref(), Some("B2B SaaS"));
    }

    #[test]
    fn specimen_6_example_saas_segment_and_audience_uninterpretable() {
        // Target "manufacturing" was chosen (not "b2b saas") because the
        // b2b overlap would otherwise make audience interpretable via the
        // standalone b2b token inside "b2b saas". "manufacturing" leaves
        // only industry interpretable, which is what this specimen
        // demonstrates.
        let out = qualify(&example_saas("manufacturing"));
        assert_eq!(out.next_action, NextAction::NeedsEnrichment);
        assert_eq!(
            out.reasons[0],
            "Target ICP contains no supported terms in segment, audience"
        );
    }

    #[test]
    fn specimen_7_example_saas_all_dims_uninterpretable() {
        let out = qualify(&example_saas("selling to teams of hobbyists"));
        assert_eq!(out.next_action, NextAction::NeedsEnrichment);
        assert_eq!(
            out.reasons[0],
            "Target ICP contains no supported terms in industry, segment, audience"
        );
        // Known profile => facts still echo through.
        assert_eq!(out.company_name.as_deref(), Some("Example SaaS"));
        assert_eq!(out.industry.as_deref(), Some("B2B SaaS"));
    }

    #[test]
    fn specimen_8_unknown_profile_short_circuits() {
        let out = qualify(&unknown("b2b saas smb creators"));
        assert_eq!(out.next_action, NextAction::NeedsEnrichment);
        assert_eq!(out.reasons.len(), 1);
        assert_eq!(out.reasons[0], "Company domain not in catalog");
        assert_eq!(out.company_name, None);
        assert_eq!(out.industry, None);
        assert_eq!(out.icp_match, None);
        assert_eq!(out.score, None);
    }

    #[test]
    fn specimen_9_shop_example_full_match() {
        let out = qualify(&shop_example("retail smb b2c"));
        assert_eq!(out.next_action, NextAction::Qualified);
        assert_eq!(out.score.unwrap().to_bits(), (3.0f64 / 3.0f64).to_bits());
        assert_eq!(out.company_name.as_deref(), Some("Shop Example"));
        assert_eq!(out.industry.as_deref(), Some("retail"));
    }

    #[test]
    fn specimen_10_factory_example_full_match() {
        let out = qualify(&factory_example("manufacturing enterprise b2b"));
        assert_eq!(out.next_action, NextAction::Qualified);
        assert_eq!(out.score.unwrap().to_bits(), (3.0f64 / 3.0f64).to_bits());
        assert_eq!(out.company_name.as_deref(), Some("Factory Example"));
        assert_eq!(out.industry.as_deref(), Some("manufacturing"));
    }

    #[test]
    fn specimen_11_strategy_a_segment_conflict_tolerated() {
        let out = qualify(&example_saas("smb enterprise b2b saas creators"));
        assert_eq!(out.next_action, NextAction::Qualified);
        assert_eq!(out.score.unwrap().to_bits(), (3.0f64 / 3.0f64).to_bits());
        // Segment reason must disclose both recognized descriptors in
        // canonical order (smb before enterprise).
        assert_eq!(
            out.reasons[1],
            "Segment matches: smb (target recognizes smb, enterprise)"
        );
    }

    // ---- Three explicit assertions from scoring review ------------------

    #[test]
    fn reversing_descriptor_order_leaves_outcome_byte_identical() {
        // Descriptor-order independence (NOT raw token reversal).
        // Multi-word "b2b saas" is kept contiguous in both targets so
        // the matching contract isn't violated.
        //   forward: smb (seg) | enterprise (seg) | b2b saas (ind) | creators (aud)
        //   reverse: creators (aud) | b2b saas (ind) | enterprise (seg) | smb (seg)
        let forward = qualify(&example_saas("smb enterprise b2b saas creators"));
        let reverse = qualify(&example_saas("creators b2b saas enterprise smb"));
        assert_eq!(forward.next_action, reverse.next_action);
        assert_eq!(forward.icp_match, reverse.icp_match);
        assert_eq!(
            forward.score.unwrap().to_bits(),
            reverse.score.unwrap().to_bits()
        );
        // Reason bytes must be byte-identical -- canonical-order
        // "target recognizes <list>" is independent of wire order.
        assert_eq!(forward.reasons, reverse.reasons);
    }

    #[test]
    fn repeating_a_recognized_descriptor_leaves_outcome_identical() {
        let once = qualify(&example_saas("b2b saas smb creators"));
        let twice = qualify(&example_saas("b2b saas smb creators smb creators b2b saas"));
        assert_eq!(once.next_action, twice.next_action);
        assert_eq!(once.icp_match, twice.icp_match);
        assert_eq!(
            once.score.unwrap().to_bits(),
            twice.score.unwrap().to_bits()
        );
        assert_eq!(once.reasons, twice.reasons);
    }

    #[test]
    fn multi_descriptor_match_reports_all_alternatives_in_canonical_order() {
        // Segment vocab canonical order: [smb, mid-market, enterprise].
        // Target contains smb and enterprise (not mid-market).
        // Recognized filtered to canonical order: [smb, enterprise].
        let out = qualify(&example_saas("b2b saas smb enterprise creators"));
        assert_eq!(
            out.reasons[1],
            "Segment matches: smb (target recognizes smb, enterprise)"
        );
    }

    // ---- Hyphenated descriptor edge cases ------------------------------

    #[test]
    fn mid_market_as_single_hyphenated_token_matches_segment() {
        let p = pi(
            true,
            Some("Mid Co"),
            Some("B2B SaaS"),
            Some("Mid-Market"),
            Some("creators"),
            "b2b saas mid-market creators",
        );
        let out = qualify(&p);
        assert_eq!(out.next_action, NextAction::Qualified);
        assert_eq!(out.score.unwrap().to_bits(), (3.0f64 / 3.0f64).to_bits());
        assert_eq!(out.reasons[1], "Segment matches: mid-market");
    }

    #[test]
    fn mid_space_market_two_tokens_is_not_recognized() {
        let p = pi(
            true,
            Some("Mid Co"),
            Some("B2B SaaS"),
            Some("Mid-Market"),
            Some("creators"),
            "b2b saas mid market creators",
        );
        let out = qualify(&p);
        // Segment uninterpretable (no segment descriptor present).
        assert_eq!(out.next_action, NextAction::NeedsEnrichment);
        assert_eq!(
            out.reasons[0],
            "Target ICP contains no supported terms in segment"
        );
    }

    // ---- Known-profile with zero matches must NOT fall back ------------

    #[test]
    fn shop_example_zero_match_is_not_qualified() {
        // Shop Example: segment=SMB, audience=B2C, industry=retail.
        // Target: all three dims interpretable but zero match.
        let out = qualify(&shop_example("b2b saas enterprise b2b"));
        assert_eq!(out.next_action, NextAction::NotQualified);
        assert_eq!(out.score.unwrap().to_bits(), (0.0f64 / 3.0f64).to_bits());
    }

    #[test]
    fn factory_example_zero_match_is_not_qualified() {
        // Factory profile: industry=manufacturing, segment=enterprise,
        // audience=b2b. Target "retail smb creators": all three dims
        // interpretable (retail, smb, creators are each in a different
        // vocab), none match Factory. matched=0 -> not_qualified
        // (NOT needs_enrichment -- the "no fallback" rule).
        let out = qualify(&factory_example("retail smb creators"));
        assert_eq!(out.next_action, NextAction::NotQualified);
        assert_eq!(out.score.unwrap().to_bits(), (0.0f64 / 3.0f64).to_bits());
    }

    // ---- Reason length budget (schema maxLength 200) ------------------

    #[test]
    fn all_reasons_satisfy_200_codepoint_schema_limit() {
        // Exhaustive combination over profiles x adversarial target ICPs.
        let targets: &[&str] = &[
            "b2b saas smb creators",
            "manufacturing enterprise b2b",
            "retail smb b2c",
            "smb enterprise b2b saas creators",
            // Maximally crowded ICP: every descriptor present at least once.
            "b2b saas manufacturing retail smb mid-market enterprise b2b b2c creators",
            // Repetition stressing the maximum token count.
            "b2b saas manufacturing retail smb mid-market enterprise b2b b2c creators \
             b2b saas manufacturing retail smb mid-market enterprise b2b b2c creators",
            "selling to teams of hobbyists",
            "b2b saas creators",
            "manufacturing enterprise b2c",
            "b2b saas",
            "",
        ];
        // Also include an empty target to exercise an empty-token path.
        // parse_input rejects empty target_icp, but qualify may still be
        // called defensively; validate it doesn't produce a > 200 cp reason.
        let profiles: &[ParsedInput<'static>] = &[
            example_saas(""),
            shop_example(""),
            factory_example(""),
            unknown(""),
        ];
        for p in profiles {
            for t in targets {
                let p_with_t = pi(
                    p.profile_found,
                    p.company_name,
                    p.industry,
                    p.segment,
                    p.audience,
                    t,
                );
                let out = qualify(&p_with_t);
                for reason in &out.reasons {
                    let cp = reason.chars().count();
                    assert!(
                        cp <= 200,
                        "reason exceeds 200 code points ({} cp): {}",
                        cp,
                        reason
                    );
                }
            }
        }
    }

    // ---- Exact f64 bit patterns ----------------------------------------
    //
    // All reachable scores in v0.1 are (matched as f64) / 3.0 for matched
    // in {0, 1, 2, 3}. Assert bit-exactness against the inline recomputed
    // value, never against truncated decimal literals.

    #[test]
    fn score_bit_pattern_zero_of_three() {
        let out = qualify(&example_saas("manufacturing enterprise b2c"));
        assert_eq!(out.score.unwrap().to_bits(), (0.0f64 / 3.0f64).to_bits());
    }

    #[test]
    fn score_bit_pattern_one_of_three() {
        let out = qualify(&example_saas("manufacturing smb b2b"));
        assert_eq!(out.score.unwrap().to_bits(), (1.0f64 / 3.0f64).to_bits());
    }

    #[test]
    fn score_bit_pattern_two_of_three() {
        let out = qualify(&example_saas("b2b saas smb b2b"));
        assert_eq!(out.score.unwrap().to_bits(), (2.0f64 / 3.0f64).to_bits());
    }

    #[test]
    fn score_bit_pattern_three_of_three() {
        let out = qualify(&example_saas("b2b saas smb creators"));
        assert_eq!(out.score.unwrap().to_bits(), (3.0f64 / 3.0f64).to_bits());
    }

    // ---- NextAction::as_str canonical mapping --------------------------

    #[test]
    fn next_action_as_str_maps_to_canonical_schema_values() {
        assert_eq!(NextAction::Qualified.as_str(), "qualified");
        assert_eq!(NextAction::NotQualified.as_str(), "not_qualified");
        assert_eq!(NextAction::NeedsEnrichment.as_str(), "needs_enrichment");
    }

    // ---- Audience "b2b" overlap with industry "b2b saas" ---------------

    #[test]
    fn b2b_token_counts_for_both_industry_prefix_and_audience_standalone() {
        // Profile audience = "b2b". Target "b2b saas smb b2b":
        //   industry "b2b saas" matches (contiguous positions 0..2)
        //   audience "b2b"      matches (standalone token at position 0 and 3)
        let p = pi(
            true,
            Some("Dual"),
            Some("B2B SaaS"),
            Some("SMB"),
            Some("B2B"),
            "b2b saas smb b2b",
        );
        let out = qualify(&p);
        assert_eq!(out.next_action, NextAction::Qualified);
        assert_eq!(out.score.unwrap().to_bits(), (3.0f64 / 3.0f64).to_bits());
        assert_eq!(out.reasons[0], "Industry matches: b2b saas");
        assert_eq!(out.reasons[2], "Audience matches: b2b");
    }

    // ---- Punctuation attached to tokens is NOT ignored ------------------

    #[test]
    fn punctuation_attached_to_descriptor_prevents_match() {
        // "retail," is a single token "retail," (comma attached). It is
        // NOT the literal descriptor "retail". Dimension uninterpretable.
        let p = pi(
            true,
            Some("Shop"),
            Some("retail"),
            Some("SMB"),
            Some("B2C"),
            "retail, smb b2c",
        );
        let out = qualify(&p);
        // Industry dim has no recognized descriptor.
        assert_eq!(out.next_action, NextAction::NeedsEnrichment);
        assert_eq!(
            out.reasons[0],
            "Target ICP contains no supported terms in industry"
        );
    }

    // ---- Canonical-order independence from target_icp order ------------

    #[test]
    fn canonical_recognized_order_is_vocab_order_not_wire_order() {
        // Target lists audience descriptors in reverse: creators b2c b2b.
        // Reason's "target recognizes <list>" must list them in canonical
        // vocab order: b2b, b2c, creators -- NOT wire order.
        let p = pi(
            true,
            Some("TestCo"),
            Some("manufacturing"),
            Some("smb"),
            Some("creators"),
            "manufacturing smb creators b2c b2b",
        );
        let out = qualify(&p);
        // audience_match: profile=creators is in {b2b, b2c, creators} => match.
        // Multi-descriptor match => reason discloses all three in canonical order.
        assert_eq!(
            out.reasons[2],
            "Audience matches: creators (target recognizes b2b, b2c, creators)"
        );
    }
}

// =========================================================================
// Section 3 -- Output encoding + Model B ABI exports
// =========================================================================
//
// Result envelope (same shape as brick 1):
//   { "type": "Success", "output": <6-field map> }
//   { "type": "Failure", "error":  { "error_class": "INVALID_INPUT",
//                                    "message": <string> } }
//
// Success "output" has exactly 6 fields in alphabetical key order:
//   company_name   : text or null
//   icp_match      : bool or null (null iff next_action=needs_enrichment)
//   industry       : text or null
//   next_action    : text in {"qualified","not_qualified","needs_enrichment"}
//   reasons        : array of 1..=5 text strings
//   score          : f64 in [0,1] or null (null iff next_action=needs_enrichment)
//
// ABI (Model B, identical to brick 1):
//   alloc(len)                        -> i32 ptr (aligned 4)
//   free(ptr, len)
//   invoke(envelope_ptr, envelope_len) -> i32 ptr to {u32 cbor_len | cbor_bytes}
//
// write_result allocates 4+cbor_len bytes, writes a little-endian u32
// length prefix followed by the CBOR result bytes, and returns the
// pointer. Length arithmetic uses `checked_add` + `i32::try_from` to
// surface overflow as a controlled wasm32 unreachable (never a cast
// truncation).

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

    // Bump allocator with correct ABSOLUTE-ADDRESS alignment (mirrors
    // brick 1's fix): aligning the raw heap offset is unsound because the
    // heap's base address may not itself be aligned to the requested
    // layout.align(). Instead, compute the current absolute address and
    // align THAT; the padding needed is the delta between the aligned
    // absolute address and the current absolute address.
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

/// Encode a complete Success result for a ScoreOutcome.
///
/// Six-field "output" map in alphabetical key order:
///   company_name, icp_match, industry, next_action, reasons, score.
/// All operations are allocation-local; no shared/global state is read.
pub(crate) fn build_success(outcome: &ScoreOutcome) -> Vec<u8> {
    let mut buf = Vec::new();
    let mut enc = minicbor::Encoder::new(&mut buf);

    enc.map(2).unwrap();

    enc.str("output").unwrap();
    enc.map(6).unwrap();

    enc.str("company_name").unwrap();
    match &outcome.company_name {
        Some(s) => {
            enc.str(s).unwrap();
        }
        None => {
            enc.null().unwrap();
        }
    }

    enc.str("icp_match").unwrap();
    match outcome.icp_match {
        Some(b) => {
            enc.bool(b).unwrap();
        }
        None => {
            enc.null().unwrap();
        }
    }

    enc.str("industry").unwrap();
    match &outcome.industry {
        Some(s) => {
            enc.str(s).unwrap();
        }
        None => {
            enc.null().unwrap();
        }
    }

    enc.str("next_action").unwrap();
    enc.str(outcome.next_action.as_str()).unwrap();

    enc.str("reasons").unwrap();
    enc.array(outcome.reasons.len() as u64).unwrap();
    for r in &outcome.reasons {
        enc.str(r).unwrap();
    }

    enc.str("score").unwrap();
    match outcome.score {
        Some(s) => {
            enc.f64(s).unwrap();
        }
        None => {
            enc.null().unwrap();
        }
    }

    enc.str("type").unwrap();
    enc.str("Success").unwrap();

    buf
}

/// Encode a Failure(INVALID_INPUT) result. The caller-supplied `message`
/// becomes `error.message`; `error.error_class` is always "INVALID_INPUT".
///
/// Parser failures and envelope-level failures BOTH route here; neither
/// is ever converted into a Success/needs_enrichment outcome.
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

/// Full host-visible entry point: parse the envelope, qualify, encode.
///
/// Validates the complete input first (parse_input). On parse error,
/// returns Failure(INVALID_INPUT) with the error's message. On parse
/// success, runs qualify() and returns Success with the ScoreOutcome.
/// Known-profile facts are preserved in output even when the outcome
/// is needs_enrichment-via-ICP (ScoreOutcome carries them).
pub(crate) fn process_envelope(envelope: &[u8]) -> Vec<u8> {
    let parsed = match parse_input(envelope) {
        Ok(p) => p,
        Err(e) => return build_failure_invalid_input(&e.message()),
    };
    let outcome = qualify(&parsed);
    build_success(&outcome)
}

#[cfg(target_arch = "wasm32")]
use core::slice;

/// Pack a CBOR result into the length-prefixed buffer expected by the
/// host: 4-byte little-endian u32 cbor_len, then cbor_len bytes of CBOR.
/// Returns the allocated buffer's pointer (i32). Overflow at any step
/// triggers wasm32 unreachable rather than a truncating cast.
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

// ---- Section 3 host-side unit tests ------------------------------------

#[cfg(all(test, not(target_arch = "wasm32")))]
mod section3_tests {
    use super::*;
    use minicbor::{Decoder, Encoder};

    // ---- Result-walking helpers ----------------------------------------

    /// Decoded Success envelope. All six output fields recorded as either
    /// Text/Null/Bool/F64/Array; test-side verification picks the field
    /// of interest.
    #[derive(Debug)]
    struct SuccessOut {
        company_name: Option<String>,
        icp_match: Option<bool>,
        industry: Option<String>,
        next_action: String,
        reasons: Vec<String>,
        score: Option<f64>,
        /// Order in which keys appeared on the wire. Must be alphabetical.
        key_order: Vec<String>,
    }

    /// Walk a result envelope and either return a SuccessOut or an
    /// (error_class, message) pair for Failure.
    #[derive(Debug)]
    enum Decoded {
        Success(SuccessOut),
        Failure {
            error_class: String,
            message: String,
        },
    }

    fn decode_result(bytes: &[u8]) -> Decoded {
        let mut dec = Decoder::new(bytes);
        let envelope_len = dec.map().unwrap().unwrap();
        assert_eq!(envelope_len, 2, "result envelope must have 2 fields");

        let mut typ: Option<String> = None;
        let mut output_bytes: Option<Vec<u8>> = None;
        let mut error_bytes: Option<Vec<u8>> = None;

        for _ in 0..envelope_len {
            let key = dec.str().unwrap().to_string();
            let start = dec.position();
            dec.skip().unwrap();
            let end = dec.position();
            match key.as_str() {
                "type" => {
                    let mut d = Decoder::new(&bytes[start..end]);
                    typ = Some(d.str().unwrap().to_string());
                }
                "output" => {
                    output_bytes = Some(bytes[start..end].to_vec());
                }
                "error" => {
                    error_bytes = Some(bytes[start..end].to_vec());
                }
                other => panic!("unexpected envelope key: {}", other),
            }
        }
        assert_eq!(dec.position(), bytes.len(), "no trailing bytes");

        match typ.as_deref() {
            Some("Success") => {
                let out = output_bytes.expect("Success must carry output");
                Decoded::Success(decode_success_output(&out))
            }
            Some("Failure") => {
                let err = error_bytes.expect("Failure must carry error");
                let mut d = Decoder::new(&err);
                let len = d.map().unwrap().unwrap();
                assert_eq!(len, 2);
                let mut error_class = None;
                let mut message = None;
                for _ in 0..len {
                    let k = d.str().unwrap().to_string();
                    let v = d.str().unwrap().to_string();
                    match k.as_str() {
                        "error_class" => error_class = Some(v),
                        "message" => message = Some(v),
                        other => panic!("unexpected error key: {}", other),
                    }
                }
                Decoded::Failure {
                    error_class: error_class.unwrap(),
                    message: message.unwrap(),
                }
            }
            other => panic!("unexpected type: {:?}", other),
        }
    }

    fn decode_success_output(bytes: &[u8]) -> SuccessOut {
        let mut dec = Decoder::new(bytes);
        let len = dec.map().unwrap().unwrap();
        assert_eq!(len, 6, "output map must have exactly 6 fields");
        let mut key_order = Vec::new();
        let mut company_name = None;
        let mut icp_match = None;
        let mut industry = None;
        let mut next_action = None;
        let mut reasons = None;
        let mut score = None;
        for _ in 0..len {
            let k = dec.str().unwrap().to_string();
            key_order.push(k.clone());
            match k.as_str() {
                "company_name" => company_name = Some(decode_text_or_null(&mut dec)),
                "icp_match" => icp_match = Some(decode_bool_or_null(&mut dec)),
                "industry" => industry = Some(decode_text_or_null(&mut dec)),
                "next_action" => next_action = Some(dec.str().unwrap().to_string()),
                "reasons" => {
                    let n = dec.array().unwrap().unwrap();
                    let mut v = Vec::new();
                    for _ in 0..n {
                        v.push(dec.str().unwrap().to_string());
                    }
                    reasons = Some(v);
                }
                "score" => score = Some(decode_f64_or_null(&mut dec)),
                other => panic!("unexpected output key: {}", other),
            }
        }
        assert_eq!(dec.position(), bytes.len(), "no trailing bytes in output");
        SuccessOut {
            company_name: company_name.unwrap(),
            icp_match: icp_match.unwrap(),
            industry: industry.unwrap(),
            next_action: next_action.unwrap(),
            reasons: reasons.unwrap(),
            score: score.unwrap(),
            key_order,
        }
    }

    fn decode_text_or_null(dec: &mut Decoder<'_>) -> Option<String> {
        match dec.datatype().unwrap() {
            Type::Null => {
                dec.skip().unwrap();
                None
            }
            Type::String => Some(dec.str().unwrap().to_string()),
            other => panic!("expected text or null, got {:?}", other),
        }
    }

    fn decode_bool_or_null(dec: &mut Decoder<'_>) -> Option<bool> {
        match dec.datatype().unwrap() {
            Type::Null => {
                dec.skip().unwrap();
                None
            }
            Type::Bool => Some(dec.bool().unwrap()),
            other => panic!("expected bool or null, got {:?}", other),
        }
    }

    fn decode_f64_or_null(dec: &mut Decoder<'_>) -> Option<f64> {
        match dec.datatype().unwrap() {
            Type::Null => {
                dec.skip().unwrap();
                None
            }
            Type::F64 => Some(dec.f64().unwrap()),
            other => panic!("expected f64 or null, got {:?}", other),
        }
    }

    // ---- Test fixtures mirroring Section 2 -----------------------------

    fn pi<'a>(
        profile_found: bool,
        company_name: Option<&'a str>,
        industry: Option<&'a str>,
        segment: Option<&'a str>,
        audience: Option<&'a str>,
        target_icp: &'a str,
    ) -> ParsedInput<'a> {
        ParsedInput {
            company_name,
            industry,
            segment,
            audience,
            profile_found,
            target_icp,
            notes: None,
        }
    }
    fn example_saas(target_icp: &str) -> ParsedInput<'_> {
        pi(
            true,
            Some("Example SaaS"),
            Some("B2B SaaS"),
            Some("SMB"),
            Some("creators"),
            target_icp,
        )
    }
    fn unknown(target_icp: &str) -> ParsedInput<'_> {
        pi(false, None, None, None, None, target_icp)
    }

    /// Build a valid envelope wrapping a 7-field application input.
    fn envelope_wrapping(input_cbor: &[u8]) -> Vec<u8> {
        let mut buf = Vec::new();
        {
            let mut enc = Encoder::new(&mut buf);
            enc.map(1).unwrap();
            enc.str("input").unwrap();
        }
        buf.extend_from_slice(input_cbor);
        buf
    }

    fn good_app_found() -> Vec<u8> {
        let mut buf = Vec::new();
        let mut enc = Encoder::new(&mut buf);
        enc.map(7).unwrap();
        enc.str("audience").unwrap().str("creators").unwrap();
        enc.str("company_name")
            .unwrap()
            .str("Example SaaS")
            .unwrap();
        enc.str("industry").unwrap().str("B2B SaaS").unwrap();
        enc.str("notes").unwrap().null().unwrap();
        enc.str("profile_found").unwrap().bool(true).unwrap();
        enc.str("segment").unwrap().str("SMB").unwrap();
        enc.str("target_icp")
            .unwrap()
            .str("b2b saas smb creators")
            .unwrap();
        buf
    }

    // ---- 1. Six-field CBOR contract: all three Success outcomes --------

    #[test]
    fn build_success_qualified_has_exactly_six_fields_alphabetical() {
        let outcome = qualify(&example_saas("b2b saas smb creators"));
        let bytes = build_success(&outcome);
        let decoded = decode_result(&bytes);
        let out = match decoded {
            Decoded::Success(s) => s,
            Decoded::Failure { .. } => panic!("expected Success"),
        };
        assert_eq!(
            out.key_order,
            alloc::vec![
                "company_name".to_string(),
                "icp_match".to_string(),
                "industry".to_string(),
                "next_action".to_string(),
                "reasons".to_string(),
                "score".to_string(),
            ]
        );
    }

    #[test]
    fn build_success_qualified_field_values() {
        let outcome = qualify(&example_saas("b2b saas smb creators"));
        let bytes = build_success(&outcome);
        let out = match decode_result(&bytes) {
            Decoded::Success(s) => s,
            _ => panic!(),
        };
        assert_eq!(out.company_name.as_deref(), Some("Example SaaS"));
        assert_eq!(out.icp_match, Some(true));
        assert_eq!(out.industry.as_deref(), Some("B2B SaaS"));
        assert_eq!(out.next_action, "qualified");
        assert_eq!(out.reasons.len(), 3);
        // Score bit-exact against (3.0 / 3.0).
        assert_eq!(out.score.unwrap().to_bits(), (3.0f64 / 3.0f64).to_bits());
    }

    #[test]
    fn build_success_not_qualified_field_values() {
        let outcome = qualify(&example_saas("manufacturing enterprise b2c"));
        let bytes = build_success(&outcome);
        let out = match decode_result(&bytes) {
            Decoded::Success(s) => s,
            _ => panic!(),
        };
        assert_eq!(out.company_name.as_deref(), Some("Example SaaS"));
        assert_eq!(out.icp_match, Some(false));
        assert_eq!(out.industry.as_deref(), Some("B2B SaaS"));
        assert_eq!(out.next_action, "not_qualified");
        assert_eq!(out.reasons.len(), 3);
        assert_eq!(out.score.unwrap().to_bits(), (0.0f64 / 3.0f64).to_bits());
    }

    #[test]
    fn build_success_needs_enrichment_unknown_profile() {
        let outcome = qualify(&unknown("b2b saas smb creators"));
        let bytes = build_success(&outcome);
        let out = match decode_result(&bytes) {
            Decoded::Success(s) => s,
            _ => panic!(),
        };
        // Unknown profile: both company_name and industry are null.
        assert_eq!(out.company_name, None);
        assert_eq!(out.icp_match, None);
        assert_eq!(out.industry, None);
        assert_eq!(out.next_action, "needs_enrichment");
        assert_eq!(out.reasons, alloc::vec!["Company domain not in catalog"]);
        assert_eq!(out.score, None);
    }

    #[test]
    fn build_success_needs_enrichment_icp_insufficient_preserves_facts() {
        // Known profile, ICP uninterpretable: facts STAY nonempty.
        // This is the "preserve company facts for known profiles even
        // when outcome is needs_enrichment" rule.
        let outcome = qualify(&example_saas("selling to teams of hobbyists"));
        let bytes = build_success(&outcome);
        let out = match decode_result(&bytes) {
            Decoded::Success(s) => s,
            _ => panic!(),
        };
        assert_eq!(out.company_name.as_deref(), Some("Example SaaS"));
        assert_eq!(out.icp_match, None);
        assert_eq!(out.industry.as_deref(), Some("B2B SaaS"));
        assert_eq!(out.next_action, "needs_enrichment");
        assert_eq!(
            out.reasons,
            alloc::vec![
                "Target ICP contains no supported terms in industry, segment, audience".to_string()
            ]
        );
        assert_eq!(out.score, None);
    }

    // ---- 2. Failure(INVALID_INPUT) -------------------------------------

    #[test]
    fn build_failure_envelope_shape() {
        let bytes = build_failure_invalid_input("test message");
        let d = decode_result(&bytes);
        match d {
            Decoded::Failure {
                error_class,
                message,
            } => {
                assert_eq!(error_class, "INVALID_INPUT");
                assert_eq!(message, "test message");
            }
            _ => panic!("expected Failure"),
        }
    }

    #[test]
    fn process_envelope_malformed_cbor_yields_failure_invalid_input() {
        // Scalar envelope -- not a map.
        let d = decode_result(&process_envelope(&[0x1Au8, 0, 0, 0, 42]));
        match d {
            Decoded::Failure { error_class, .. } => {
                assert_eq!(error_class, "INVALID_INPUT");
            }
            _ => panic!("expected Failure"),
        }
    }

    #[test]
    fn process_envelope_invalid_input_never_converts_to_needs_enrichment() {
        // Craft envelope with profile_found=false but non-null company_name.
        // Parser's Pass 3 rejects as FactsMustBeNullWhenNotFound ->
        // Failure(INVALID_INPUT). Must NOT silently become Success/needs_enrichment.
        let mut inner = Vec::new();
        let mut enc = Encoder::new(&mut inner);
        enc.map(7).unwrap();
        enc.str("audience").unwrap().null().unwrap();
        enc.str("company_name").unwrap().str("Ghost").unwrap(); // violates Pass 3
        enc.str("industry").unwrap().null().unwrap();
        enc.str("notes").unwrap().null().unwrap();
        enc.str("profile_found").unwrap().bool(false).unwrap();
        enc.str("segment").unwrap().null().unwrap();
        enc.str("target_icp").unwrap().str("x").unwrap();
        let env = envelope_wrapping(&inner);
        let d = decode_result(&process_envelope(&env));
        match d {
            Decoded::Failure { error_class, .. } => {
                assert_eq!(error_class, "INVALID_INPUT")
            }
            Decoded::Success(_) => panic!("parser failure must not become Success"),
        }
    }

    #[test]
    fn process_envelope_targeticp_empty_yields_failure_not_needs_enrichment() {
        // Known profile but target_icp="" -- parser rejects at TargetIcpEmpty.
        let mut inner = Vec::new();
        let mut enc = Encoder::new(&mut inner);
        enc.map(7).unwrap();
        enc.str("audience").unwrap().str("creators").unwrap();
        enc.str("company_name")
            .unwrap()
            .str("Example SaaS")
            .unwrap();
        enc.str("industry").unwrap().str("B2B SaaS").unwrap();
        enc.str("notes").unwrap().null().unwrap();
        enc.str("profile_found").unwrap().bool(true).unwrap();
        enc.str("segment").unwrap().str("SMB").unwrap();
        enc.str("target_icp").unwrap().str("").unwrap();
        let env = envelope_wrapping(&inner);
        let d = decode_result(&process_envelope(&env));
        match d {
            Decoded::Failure { .. } => {}
            Decoded::Success(_) => panic!("empty target_icp must be a Failure"),
        }
    }

    // ---- 3. End-to-end through process_envelope ------------------------

    #[test]
    fn process_envelope_qualified_full_path() {
        let env = envelope_wrapping(&good_app_found());
        let d = decode_result(&process_envelope(&env));
        let out = match d {
            Decoded::Success(s) => s,
            _ => panic!(),
        };
        assert_eq!(out.next_action, "qualified");
        assert_eq!(out.icp_match, Some(true));
        assert_eq!(out.score.unwrap().to_bits(), (3.0f64 / 3.0f64).to_bits());
    }

    #[test]
    fn process_envelope_not_qualified_full_path() {
        // Known profile, zero match (all 3 dims interpretable).
        let mut inner = Vec::new();
        let mut enc = Encoder::new(&mut inner);
        enc.map(7).unwrap();
        enc.str("audience").unwrap().str("creators").unwrap();
        enc.str("company_name")
            .unwrap()
            .str("Example SaaS")
            .unwrap();
        enc.str("industry").unwrap().str("B2B SaaS").unwrap();
        enc.str("notes").unwrap().null().unwrap();
        enc.str("profile_found").unwrap().bool(true).unwrap();
        enc.str("segment").unwrap().str("SMB").unwrap();
        enc.str("target_icp")
            .unwrap()
            .str("manufacturing enterprise b2c")
            .unwrap();
        let env = envelope_wrapping(&inner);
        let d = decode_result(&process_envelope(&env));
        let out = match d {
            Decoded::Success(s) => s,
            _ => panic!(),
        };
        assert_eq!(out.next_action, "not_qualified");
        assert_eq!(out.icp_match, Some(false));
        assert_eq!(out.score.unwrap().to_bits(), (0.0f64 / 3.0f64).to_bits());
    }

    #[test]
    fn process_envelope_needs_enrichment_unknown_full_path() {
        let mut inner = Vec::new();
        let mut enc = Encoder::new(&mut inner);
        enc.map(7).unwrap();
        enc.str("audience").unwrap().null().unwrap();
        enc.str("company_name").unwrap().null().unwrap();
        enc.str("industry").unwrap().null().unwrap();
        enc.str("notes").unwrap().null().unwrap();
        enc.str("profile_found").unwrap().bool(false).unwrap();
        enc.str("segment").unwrap().null().unwrap();
        enc.str("target_icp")
            .unwrap()
            .str("b2b saas smb creators")
            .unwrap();
        let env = envelope_wrapping(&inner);
        let d = decode_result(&process_envelope(&env));
        let out = match d {
            Decoded::Success(s) => s,
            _ => panic!(),
        };
        assert_eq!(out.next_action, "needs_enrichment");
        assert_eq!(out.company_name, None);
        assert_eq!(out.industry, None);
        assert_eq!(out.icp_match, None);
        assert_eq!(out.score, None);
        assert_eq!(out.reasons.len(), 1);
    }

    #[test]
    fn process_envelope_needs_enrichment_icp_preserves_facts() {
        // Known profile, uninterpretable ICP.
        let mut inner = Vec::new();
        let mut enc = Encoder::new(&mut inner);
        enc.map(7).unwrap();
        enc.str("audience").unwrap().str("creators").unwrap();
        enc.str("company_name")
            .unwrap()
            .str("Example SaaS")
            .unwrap();
        enc.str("industry").unwrap().str("B2B SaaS").unwrap();
        enc.str("notes").unwrap().null().unwrap();
        enc.str("profile_found").unwrap().bool(true).unwrap();
        enc.str("segment").unwrap().str("SMB").unwrap();
        enc.str("target_icp")
            .unwrap()
            .str("selling to teams of hobbyists")
            .unwrap();
        let env = envelope_wrapping(&inner);
        let d = decode_result(&process_envelope(&env));
        let out = match d {
            Decoded::Success(s) => s,
            _ => panic!(),
        };
        assert_eq!(out.next_action, "needs_enrichment");
        // Known-profile facts preserved even on needs_enrichment-via-ICP.
        assert_eq!(out.company_name.as_deref(), Some("Example SaaS"));
        assert_eq!(out.industry.as_deref(), Some("B2B SaaS"));
    }

    // ---- 4. Nullable field encoding ------------------------------------

    #[test]
    fn score_null_encodes_as_cbor_null_in_needs_enrichment() {
        let outcome = qualify(&unknown("x"));
        let bytes = build_success(&outcome);
        // byte-level: find "score" and verify the next byte is CBOR null (0xF6).
        // (We decode via helper above to confirm Option<f64> = None.)
        let out = match decode_result(&bytes) {
            Decoded::Success(s) => s,
            _ => panic!(),
        };
        assert_eq!(out.score, None);
    }

    #[test]
    fn icp_match_null_encodes_as_cbor_null_in_needs_enrichment() {
        let outcome = qualify(&unknown("x"));
        let bytes = build_success(&outcome);
        let out = match decode_result(&bytes) {
            Decoded::Success(s) => s,
            _ => panic!(),
        };
        assert_eq!(out.icp_match, None);
    }

    // ---- 5. Deterministic byte encoding --------------------------------

    #[test]
    fn build_success_is_byte_deterministic_across_repeat_invocations() {
        let outcome = qualify(&example_saas("b2b saas smb creators"));
        let a = build_success(&outcome);
        let b = build_success(&outcome);
        assert_eq!(a, b);
    }

    #[test]
    fn process_envelope_is_byte_deterministic() {
        let env = envelope_wrapping(&good_app_found());
        let a = process_envelope(&env);
        let b = process_envelope(&env);
        assert_eq!(a, b);
    }

    #[test]
    fn process_envelope_deterministic_across_all_three_outcome_branches() {
        // Qualified, Not qualified, Needs enrichment (unknown + ICP).
        let envs: Vec<Vec<u8>> = alloc::vec![
            {
                let mut inner = Vec::new();
                let mut enc = Encoder::new(&mut inner);
                enc.map(7).unwrap();
                enc.str("audience").unwrap().str("creators").unwrap();
                enc.str("company_name")
                    .unwrap()
                    .str("Example SaaS")
                    .unwrap();
                enc.str("industry").unwrap().str("B2B SaaS").unwrap();
                enc.str("notes").unwrap().null().unwrap();
                enc.str("profile_found").unwrap().bool(true).unwrap();
                enc.str("segment").unwrap().str("SMB").unwrap();
                enc.str("target_icp")
                    .unwrap()
                    .str("b2b saas smb creators")
                    .unwrap();
                envelope_wrapping(&inner)
            },
            {
                let mut inner = Vec::new();
                let mut enc = Encoder::new(&mut inner);
                enc.map(7).unwrap();
                enc.str("audience").unwrap().str("creators").unwrap();
                enc.str("company_name")
                    .unwrap()
                    .str("Example SaaS")
                    .unwrap();
                enc.str("industry").unwrap().str("B2B SaaS").unwrap();
                enc.str("notes").unwrap().null().unwrap();
                enc.str("profile_found").unwrap().bool(true).unwrap();
                enc.str("segment").unwrap().str("SMB").unwrap();
                enc.str("target_icp")
                    .unwrap()
                    .str("manufacturing enterprise b2c")
                    .unwrap();
                envelope_wrapping(&inner)
            },
            {
                let mut inner = Vec::new();
                let mut enc = Encoder::new(&mut inner);
                enc.map(7).unwrap();
                enc.str("audience").unwrap().null().unwrap();
                enc.str("company_name").unwrap().null().unwrap();
                enc.str("industry").unwrap().null().unwrap();
                enc.str("notes").unwrap().null().unwrap();
                enc.str("profile_found").unwrap().bool(false).unwrap();
                enc.str("segment").unwrap().null().unwrap();
                enc.str("target_icp").unwrap().str("x").unwrap();
                envelope_wrapping(&inner)
            },
        ];
        for env in &envs {
            let a = process_envelope(env);
            let b = process_envelope(env);
            assert_eq!(a, b);
        }
    }

    // ---- 6. Reason ordering and content --------------------------------

    #[test]
    fn qualified_reasons_are_industry_segment_audience_in_order() {
        let env = envelope_wrapping(&good_app_found());
        let d = decode_result(&process_envelope(&env));
        let out = match d {
            Decoded::Success(s) => s,
            _ => panic!(),
        };
        assert_eq!(out.reasons.len(), 3);
        assert!(out.reasons[0].starts_with("Industry "));
        assert!(out.reasons[1].starts_with("Segment "));
        assert!(out.reasons[2].starts_with("Audience "));
    }

    // ---- 7. Score bit patterns through full process_envelope -----------

    #[test]
    fn score_bit_pattern_full_path_two_thirds() {
        let mut inner = Vec::new();
        let mut enc = Encoder::new(&mut inner);
        enc.map(7).unwrap();
        enc.str("audience").unwrap().str("creators").unwrap();
        enc.str("company_name")
            .unwrap()
            .str("Example SaaS")
            .unwrap();
        enc.str("industry").unwrap().str("B2B SaaS").unwrap();
        enc.str("notes").unwrap().null().unwrap();
        enc.str("profile_found").unwrap().bool(true).unwrap();
        enc.str("segment").unwrap().str("SMB").unwrap();
        enc.str("target_icp")
            .unwrap()
            .str("b2b saas smb b2b")
            .unwrap();
        let env = envelope_wrapping(&inner);
        let d = decode_result(&process_envelope(&env));
        let out = match d {
            Decoded::Success(s) => s,
            _ => panic!(),
        };
        assert_eq!(out.next_action, "qualified");
        assert_eq!(out.score.unwrap().to_bits(), (2.0f64 / 3.0f64).to_bits());
    }

    // ---- 8. Alphabetical envelope key order (type > error/output) ------

    #[test]
    fn failure_envelope_key_order_is_error_then_type() {
        let bytes = build_failure_invalid_input("msg");
        let mut dec = Decoder::new(&bytes);
        let _len = dec.map().unwrap();
        let k0 = dec.str().unwrap().to_string();
        dec.skip().unwrap();
        let k1 = dec.str().unwrap().to_string();
        assert_eq!(k0, "error");
        assert_eq!(k1, "type");
    }

    #[test]
    fn success_envelope_key_order_is_output_then_type() {
        let outcome = qualify(&example_saas("b2b saas smb creators"));
        let bytes = build_success(&outcome);
        let mut dec = Decoder::new(&bytes);
        let _len = dec.map().unwrap();
        let k0 = dec.str().unwrap().to_string();
        dec.skip().unwrap();
        let k1 = dec.str().unwrap().to_string();
        assert_eq!(k0, "output");
        assert_eq!(k1, "type");
    }

    // ---- 9. Reasons array type verification ----------------------------

    #[test]
    fn reasons_field_is_cbor_array_of_text_strings() {
        let outcome = qualify(&example_saas("b2b saas smb creators"));
        let bytes = build_success(&outcome);
        // Walk to the "reasons" position and verify datatype.
        let mut dec = Decoder::new(&bytes);
        let _ = dec.map().unwrap();
        let _ = dec.str().unwrap(); // "output"
        let _ = dec.map().unwrap(); // output map
        for _ in 0..6 {
            let k = dec.str().unwrap().to_string();
            if k == "reasons" {
                assert_eq!(dec.datatype().unwrap(), Type::Array);
                let n = dec.array().unwrap().unwrap();
                for _ in 0..n {
                    assert_eq!(dec.datatype().unwrap(), Type::String);
                    let _ = dec.str().unwrap();
                }
                return;
            } else {
                dec.skip().unwrap();
            }
        }
        panic!("reasons field not found");
    }
}

// ---- Section 1 host-side unit tests ------------------------------------

#[cfg(all(test, not(target_arch = "wasm32")))]
mod section1_tests {
    use super::*;
    use alloc::vec::Vec;
    use minicbor::Encoder;

    // ---- test fixture helpers ------------------------------------------

    /// Build an envelope wrapping a user-provided application input (as
    /// a complete CBOR value). Produces a 1-key envelope map
    /// `{"input": <value>}` without the other envelope keys -- the Brick
    /// tolerates them being absent for parser-only tests.
    fn envelope_wrapping(input_cbor: &[u8]) -> Vec<u8> {
        let mut buf = Vec::new();
        {
            let mut enc = Encoder::new(&mut buf);
            enc.map(1).unwrap();
            enc.str("input").unwrap();
        }
        buf.extend_from_slice(input_cbor);
        buf
    }

    /// Build a full 7-field "found profile" application map CBOR value.
    fn good_app_found_notes_null() -> Vec<u8> {
        let mut buf = Vec::new();
        let mut enc = Encoder::new(&mut buf);
        enc.map(7).unwrap();
        enc.str("audience").unwrap().str("creators").unwrap();
        enc.str("company_name")
            .unwrap()
            .str("Example SaaS")
            .unwrap();
        enc.str("industry").unwrap().str("b2b saas").unwrap();
        enc.str("notes").unwrap().null().unwrap();
        enc.str("profile_found").unwrap().bool(true).unwrap();
        enc.str("segment").unwrap().str("smb").unwrap();
        enc.str("target_icp")
            .unwrap()
            .str("b2b saas smb creators")
            .unwrap();
        buf
    }

    /// Build a full 7-field "unknown profile" application map CBOR value.
    fn good_app_unknown_notes_null() -> Vec<u8> {
        let mut buf = Vec::new();
        let mut enc = Encoder::new(&mut buf);
        enc.map(7).unwrap();
        enc.str("audience").unwrap().null().unwrap();
        enc.str("company_name").unwrap().null().unwrap();
        enc.str("industry").unwrap().null().unwrap();
        enc.str("notes").unwrap().null().unwrap();
        enc.str("profile_found").unwrap().bool(false).unwrap();
        enc.str("segment").unwrap().null().unwrap();
        enc.str("target_icp")
            .unwrap()
            .str("b2b saas smb creators")
            .unwrap();
        buf
    }

    /// Build an application map with per-key value overrides.
    /// Overrides are provided as raw CBOR byte slices for each field key.
    fn custom_app_map(
        audience: &[u8],
        company_name: &[u8],
        industry: &[u8],
        notes: &[u8],
        profile_found: &[u8],
        segment: &[u8],
        target_icp: &[u8],
    ) -> Vec<u8> {
        let mut buf = Vec::new();
        {
            let mut enc = Encoder::new(&mut buf);
            enc.map(7).unwrap();
        }
        push_kv(&mut buf, "audience", audience);
        push_kv(&mut buf, "company_name", company_name);
        push_kv(&mut buf, "industry", industry);
        push_kv(&mut buf, "notes", notes);
        push_kv(&mut buf, "profile_found", profile_found);
        push_kv(&mut buf, "segment", segment);
        push_kv(&mut buf, "target_icp", target_icp);
        buf
    }

    fn push_kv(buf: &mut Vec<u8>, key: &str, raw_value: &[u8]) {
        {
            let mut enc = Encoder::new(&mut *buf);
            let _ = enc.str(key);
        }
        buf.extend_from_slice(raw_value);
    }

    fn cbor_null() -> Vec<u8> {
        let mut buf = Vec::new();
        Encoder::new(&mut buf).null().unwrap();
        buf
    }
    fn cbor_text(s: &str) -> Vec<u8> {
        let mut buf = Vec::new();
        Encoder::new(&mut buf).str(s).unwrap();
        buf
    }
    fn cbor_bool(b: bool) -> Vec<u8> {
        let mut buf = Vec::new();
        Encoder::new(&mut buf).bool(b).unwrap();
        buf
    }
    fn cbor_int(i: i64) -> Vec<u8> {
        let mut buf = Vec::new();
        Encoder::new(&mut buf).i64(i).unwrap();
        buf
    }

    // =====================================================================
    // 1. Positive baselines
    // =====================================================================

    #[test]
    fn parses_found_profile_notes_null() {
        let app = good_app_found_notes_null();
        let env = envelope_wrapping(&app);
        let parsed = parse_input(&env).unwrap();
        assert_eq!(parsed.audience, Some("creators"));
        assert_eq!(parsed.company_name, Some("Example SaaS"));
        assert_eq!(parsed.industry, Some("b2b saas"));
        assert_eq!(parsed.notes, None);
        assert!(parsed.profile_found);
        assert_eq!(parsed.segment, Some("smb"));
        assert_eq!(parsed.target_icp, "b2b saas smb creators");
    }

    #[test]
    fn parses_found_profile_notes_string() {
        let app = custom_app_map(
            &cbor_text("creators"),
            &cbor_text("Example SaaS"),
            &cbor_text("b2b saas"),
            &cbor_text("Introduced by partner."),
            &cbor_bool(true),
            &cbor_text("smb"),
            &cbor_text("b2b saas smb creators"),
        );
        let env = envelope_wrapping(&app);
        let parsed = parse_input(&env).unwrap();
        assert_eq!(parsed.notes, Some("Introduced by partner."));
    }

    #[test]
    fn parses_unknown_profile_notes_null() {
        let app = good_app_unknown_notes_null();
        let env = envelope_wrapping(&app);
        let parsed = parse_input(&env).unwrap();
        assert!(!parsed.profile_found);
        assert_eq!(parsed.audience, None);
        assert_eq!(parsed.company_name, None);
        assert_eq!(parsed.industry, None);
        assert_eq!(parsed.segment, None);
        assert_eq!(parsed.notes, None);
        assert_eq!(parsed.target_icp, "b2b saas smb creators");
    }

    #[test]
    fn parses_unknown_profile_notes_string() {
        let app = custom_app_map(
            &cbor_null(),
            &cbor_null(),
            &cbor_null(),
            &cbor_text("why we're looking them up"),
            &cbor_bool(false),
            &cbor_null(),
            &cbor_text("retail smb b2c"),
        );
        let env = envelope_wrapping(&app);
        let parsed = parse_input(&env).unwrap();
        assert_eq!(parsed.notes, Some("why we're looking them up"));
    }

    #[test]
    fn parses_all_fields_at_max_length() {
        // Max-length ASCII strings per the schema (code-point counts).
        let a_32: String = "a".repeat(32);
        let a_64: String = "a".repeat(64);
        let a_256: String = "a".repeat(256);
        let a_1024: String = "a".repeat(1024);
        let app = custom_app_map(
            &cbor_text(&a_32),
            &cbor_text(&a_256),
            &cbor_text(&a_64),
            &cbor_text(&a_1024),
            &cbor_bool(true),
            &cbor_text(&a_32),
            &cbor_text(&a_1024),
        );
        let env = envelope_wrapping(&app);
        let parsed = parse_input(&env).unwrap();
        assert_eq!(parsed.audience.unwrap().len(), 32);
        assert_eq!(parsed.company_name.unwrap().len(), 256);
        assert_eq!(parsed.industry.unwrap().len(), 64);
        assert_eq!(parsed.notes.unwrap().len(), 1024);
        assert_eq!(parsed.segment.unwrap().len(), 32);
        assert_eq!(parsed.target_icp.len(), 1024);
    }

    #[test]
    fn parses_minimum_length_strings() {
        let app = custom_app_map(
            &cbor_text("a"),
            &cbor_text("a"),
            &cbor_text("a"),
            &cbor_null(),
            &cbor_bool(true),
            &cbor_text("a"),
            &cbor_text("a"),
        );
        let env = envelope_wrapping(&app);
        let parsed = parse_input(&env).unwrap();
        assert_eq!(parsed.target_icp, "a");
    }

    // =====================================================================
    // 2. Envelope-layer errors
    // =====================================================================

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
        // 0xBF is the indef-length map header.
        let buf = alloc::vec![0xBFu8, 0xFF];
        assert_eq!(parse_input(&buf), Err(InputError::IndefiniteLengthMap));
    }

    #[test]
    fn rejects_envelope_trailing_bytes() {
        let app = good_app_found_notes_null();
        let mut buf = envelope_wrapping(&app);
        buf.push(0x00); // trailing byte after outer map
        assert_eq!(parse_input(&buf), Err(InputError::TrailingBytes));
    }

    #[test]
    fn rejects_envelope_missing_input_field() {
        // Envelope map with other keys but no "input".
        let mut buf = Vec::new();
        let mut enc = Encoder::new(&mut buf);
        enc.map(1).unwrap();
        enc.str("graph_id").unwrap().str("x").unwrap();
        assert_eq!(parse_input(&buf), Err(InputError::MissingInputField));
    }

    #[test]
    fn rejects_envelope_duplicate_input_field() {
        let app = good_app_found_notes_null();
        let mut buf = Vec::new();
        {
            let mut enc = Encoder::new(&mut buf);
            enc.map(2).unwrap();
            enc.str("input").unwrap();
        }
        buf.extend_from_slice(&app);
        {
            let mut enc = Encoder::new(&mut buf);
            enc.str("input").unwrap();
        }
        buf.extend_from_slice(&app);
        assert_eq!(parse_input(&buf), Err(InputError::DuplicateInputField));
    }

    // =====================================================================
    // 3. Application-layer structural errors
    // =====================================================================

    #[test]
    fn rejects_application_indef_map() {
        let app = alloc::vec![0xBFu8, 0xFF];
        let env = envelope_wrapping(&app);
        assert_eq!(parse_input(&env), Err(InputError::IndefiniteLengthMap));
    }

    #[test]
    fn rejects_application_not_a_map() {
        let app = cbor_int(42);
        let env = envelope_wrapping(&app);
        assert_eq!(parse_input(&env), Err(InputError::InputNotMap));
    }

    #[test]
    fn rejects_application_too_many_fields() {
        // Build a 33-field application map (>= MAX_APPLICATION_FIELDS + 1).
        let mut buf = Vec::new();
        {
            let mut enc = Encoder::new(&mut buf);
            enc.map(33).unwrap();
            for i in 0..33 {
                let mut key_buf = String::new();
                key_buf.push('k');
                let digits = alloc::format!("{}", i);
                key_buf.push_str(&digits);
                enc.str(&key_buf).unwrap().i64(0).unwrap();
            }
        }
        let env = envelope_wrapping(&buf);
        assert_eq!(parse_input(&env), Err(InputError::TooManyApplicationFields));
    }

    #[test]
    fn rejects_application_trailing_bytes() {
        let app = good_app_found_notes_null();
        // Append one extra CBOR byte to the application map's slice.
        let mut app_with_trailing = app.clone();
        app_with_trailing.push(0x00);
        // We need the trailing byte INSIDE envelope.input, not after it.
        // Build a map header for a 1-field envelope manually, then
        // append the application bytes with the trailing junk as the
        // value of `input` (so skip() consumes just the map, leaving
        // trailing bytes within the value slice).
        //
        // Simpler route: wrap with envelope_wrapping, but that places
        // `input`'s value as whatever we provide. If we provide
        // "map + extra byte", skip() over the input value treats both as
        // "one value" because minicbor's skip() is a strict single-value
        // skipper -- it will stop after the map. The extra byte then
        // falls after envelope's `input` slice, outside the input slice.
        // That triggers envelope TrailingBytes, not application
        // TrailingBytesApplication.
        //
        // To trigger TrailingBytesApplication, the trailing byte must
        // live INSIDE the input slice, which means the envelope's
        // "input" value is a longer-than-the-inner-map byte stream.
        // Build a container that puts input bytes under a bytes-array
        // wrapper -- wait, that doesn't work either. Instead, declare
        // the envelope map with 1 key where input's value is: a map
        // followed by a trailing byte, both as separate top-level CBOR
        // values. envelope.skip() over the input key consumes only the
        // map; the following trailing byte is seen as the second value
        // of the input value -- but minicbor doesn't support
        // multi-value packing like that.
        //
        // The cleanest construction: embed trailing bytes INSIDE a byte
        // string, but that changes the type. The alternative is to
        // bypass extract_input_bytes and call parse_application
        // directly with a crafted slice. That's a white-box test.
        // Since parse_application is pub(crate), we can call it from
        // tests inside the same crate.
        assert_eq!(
            parse_application(&app_with_trailing),
            Err(InputError::TrailingBytesApplication)
        );
    }

    #[test]
    fn rejects_application_unknown_field() {
        // Replace one of the known fields with an unknown key.
        let mut buf = Vec::new();
        {
            let mut enc = Encoder::new(&mut buf);
            enc.map(7).unwrap();
            enc.str("audience").unwrap().str("creators").unwrap();
            enc.str("company_name")
                .unwrap()
                .str("Example SaaS")
                .unwrap();
            enc.str("industry").unwrap().str("b2b saas").unwrap();
            enc.str("notes").unwrap().null().unwrap();
            enc.str("mystery").unwrap().bool(true).unwrap(); // unknown
            enc.str("segment").unwrap().str("smb").unwrap();
            enc.str("target_icp").unwrap().str("x").unwrap();
        }
        let env = envelope_wrapping(&buf);
        assert_eq!(parse_input(&env), Err(InputError::UnknownField));
    }

    #[test]
    fn unknown_field_wins_over_duplicate_field_regardless_of_wire_order() {
        // Wire order: duplicate audience, then unknown. Expected: UnknownField.
        let mut buf = Vec::new();
        {
            let mut enc = Encoder::new(&mut buf);
            enc.map(9).unwrap();
            enc.str("audience").unwrap().str("creators").unwrap();
            enc.str("audience").unwrap().str("creators").unwrap(); // dup
            enc.str("company_name")
                .unwrap()
                .str("Example SaaS")
                .unwrap();
            enc.str("industry").unwrap().str("b2b saas").unwrap();
            enc.str("notes").unwrap().null().unwrap();
            enc.str("profile_found").unwrap().bool(true).unwrap();
            enc.str("segment").unwrap().str("smb").unwrap();
            enc.str("target_icp").unwrap().str("x").unwrap();
            enc.str("mystery").unwrap().bool(true).unwrap(); // unknown
        }
        let env = envelope_wrapping(&buf);
        assert_eq!(parse_input(&env), Err(InputError::UnknownField));
    }

    #[test]
    fn unknown_field_wins_over_duplicate_reverse_order() {
        // Wire order: unknown first, then duplicate. Expected: UnknownField.
        let mut buf = Vec::new();
        {
            let mut enc = Encoder::new(&mut buf);
            enc.map(9).unwrap();
            enc.str("mystery").unwrap().bool(true).unwrap(); // unknown
            enc.str("audience").unwrap().str("creators").unwrap();
            enc.str("audience").unwrap().str("creators").unwrap(); // dup
            enc.str("company_name")
                .unwrap()
                .str("Example SaaS")
                .unwrap();
            enc.str("industry").unwrap().str("b2b saas").unwrap();
            enc.str("notes").unwrap().null().unwrap();
            enc.str("profile_found").unwrap().bool(true).unwrap();
            enc.str("segment").unwrap().str("smb").unwrap();
            enc.str("target_icp").unwrap().str("x").unwrap();
        }
        let env = envelope_wrapping(&buf);
        assert_eq!(parse_input(&env), Err(InputError::UnknownField));
    }

    #[test]
    fn malformed_value_after_unknown_field_surfaces_malformed_cbor() {
        // Decoder's own malformed error surfaces during Pass 1 before
        // the precedence evaluation (which would otherwise return
        // UnknownField). The second entry's value is a byte-string
        // header announcing an 8-byte length suffix but EOF follows,
        // forcing minicbor's skip() to error.
        let mut inner = Vec::new();
        {
            let mut enc = Encoder::new(&mut inner);
            enc.map(2).unwrap();
            enc.str("mystery").unwrap().null().unwrap(); // benign unknown entry 1
            enc.str("other").unwrap(); // unknown key, value truncated below
        }
        inner.push(0x5B); // major type 2 (bytes), AI 27 -> 8-byte length follows
                          // (EOF -- no length bytes, no body)
        let env = envelope_wrapping(&inner);
        assert_eq!(parse_input(&env), Err(InputError::MalformedCbor));
    }

    // =====================================================================
    // 4. Duplicate per-field errors (schema-alphabetical precedence)
    // =====================================================================

    fn dup_app_map(dup_key: &str) -> Vec<u8> {
        let mut buf = Vec::new();
        let mut enc = Encoder::new(&mut buf);
        enc.map(8).unwrap(); // 7 known + 1 extra duplicate
        enc.str("audience").unwrap().str("creators").unwrap();
        enc.str("company_name")
            .unwrap()
            .str("Example SaaS")
            .unwrap();
        enc.str("industry").unwrap().str("b2b saas").unwrap();
        enc.str("notes").unwrap().null().unwrap();
        enc.str("profile_found").unwrap().bool(true).unwrap();
        enc.str("segment").unwrap().str("smb").unwrap();
        enc.str("target_icp").unwrap().str("x").unwrap();
        // Insert duplicate matching one of the keys; use a safe default
        // value for each dup (null is a valid value for most known keys).
        match dup_key {
            "profile_found" => {
                enc.str(dup_key).unwrap().bool(true).unwrap();
            }
            "target_icp" => {
                enc.str(dup_key).unwrap().str("y").unwrap();
            }
            _ => {
                enc.str(dup_key).unwrap().null().unwrap();
            }
        }
        buf
    }

    #[test]
    fn rejects_duplicate_audience() {
        let env = envelope_wrapping(&dup_app_map("audience"));
        assert_eq!(parse_input(&env), Err(InputError::DuplicateAudience));
    }
    #[test]
    fn rejects_duplicate_company_name() {
        let env = envelope_wrapping(&dup_app_map("company_name"));
        assert_eq!(parse_input(&env), Err(InputError::DuplicateCompanyName));
    }
    #[test]
    fn rejects_duplicate_industry() {
        let env = envelope_wrapping(&dup_app_map("industry"));
        assert_eq!(parse_input(&env), Err(InputError::DuplicateIndustry));
    }
    #[test]
    fn rejects_duplicate_notes() {
        let env = envelope_wrapping(&dup_app_map("notes"));
        assert_eq!(parse_input(&env), Err(InputError::DuplicateNotes));
    }
    #[test]
    fn rejects_duplicate_profile_found() {
        let env = envelope_wrapping(&dup_app_map("profile_found"));
        assert_eq!(parse_input(&env), Err(InputError::DuplicateProfileFound));
    }
    #[test]
    fn rejects_duplicate_segment() {
        let env = envelope_wrapping(&dup_app_map("segment"));
        assert_eq!(parse_input(&env), Err(InputError::DuplicateSegment));
    }
    #[test]
    fn rejects_duplicate_target_icp() {
        let env = envelope_wrapping(&dup_app_map("target_icp"));
        assert_eq!(parse_input(&env), Err(InputError::DuplicateTargetIcp));
    }

    // =====================================================================
    // 5. Missing per-field errors (schema-alphabetical precedence)
    // =====================================================================

    /// Build an application map omitting a given field. The map has 6
    /// known keys instead of 7.
    fn missing_app_map(omit: &str) -> Vec<u8> {
        let mut buf = Vec::new();
        let mut enc = Encoder::new(&mut buf);
        enc.map(6).unwrap();
        if omit != "audience" {
            enc.str("audience").unwrap().str("creators").unwrap();
        }
        if omit != "company_name" {
            enc.str("company_name")
                .unwrap()
                .str("Example SaaS")
                .unwrap();
        }
        if omit != "industry" {
            enc.str("industry").unwrap().str("b2b saas").unwrap();
        }
        if omit != "notes" {
            enc.str("notes").unwrap().null().unwrap();
        }
        if omit != "profile_found" {
            enc.str("profile_found").unwrap().bool(true).unwrap();
        }
        if omit != "segment" {
            enc.str("segment").unwrap().str("smb").unwrap();
        }
        if omit != "target_icp" {
            enc.str("target_icp").unwrap().str("x").unwrap();
        }
        buf
    }

    #[test]
    fn rejects_missing_audience() {
        let env = envelope_wrapping(&missing_app_map("audience"));
        assert_eq!(parse_input(&env), Err(InputError::MissingAudience));
    }
    #[test]
    fn rejects_missing_company_name() {
        let env = envelope_wrapping(&missing_app_map("company_name"));
        assert_eq!(parse_input(&env), Err(InputError::MissingCompanyName));
    }
    #[test]
    fn rejects_missing_industry() {
        let env = envelope_wrapping(&missing_app_map("industry"));
        assert_eq!(parse_input(&env), Err(InputError::MissingIndustry));
    }
    #[test]
    fn rejects_missing_notes() {
        let env = envelope_wrapping(&missing_app_map("notes"));
        assert_eq!(parse_input(&env), Err(InputError::MissingNotes));
    }
    #[test]
    fn rejects_missing_profile_found() {
        let env = envelope_wrapping(&missing_app_map("profile_found"));
        assert_eq!(parse_input(&env), Err(InputError::MissingProfileFound));
    }
    #[test]
    fn rejects_missing_segment() {
        let env = envelope_wrapping(&missing_app_map("segment"));
        assert_eq!(parse_input(&env), Err(InputError::MissingSegment));
    }
    #[test]
    fn rejects_missing_target_icp() {
        let env = envelope_wrapping(&missing_app_map("target_icp"));
        assert_eq!(parse_input(&env), Err(InputError::MissingTargetIcp));
    }

    // =====================================================================
    // 6. Per-field type errors (schema-alphabetical in-field precedence)
    // =====================================================================

    fn type_err_app_map(field: &str, bad_value: Vec<u8>) -> Vec<u8> {
        let aud = if field == "audience" {
            bad_value.clone()
        } else {
            cbor_text("creators")
        };
        let cn = if field == "company_name" {
            bad_value.clone()
        } else {
            cbor_text("Example SaaS")
        };
        let ind = if field == "industry" {
            bad_value.clone()
        } else {
            cbor_text("b2b saas")
        };
        let nts = if field == "notes" {
            bad_value.clone()
        } else {
            cbor_null()
        };
        let pf = if field == "profile_found" {
            bad_value.clone()
        } else {
            cbor_bool(true)
        };
        let seg = if field == "segment" {
            bad_value.clone()
        } else {
            cbor_text("smb")
        };
        let tgt = if field == "target_icp" {
            bad_value
        } else {
            cbor_text("x")
        };
        custom_app_map(&aud, &cn, &ind, &nts, &pf, &seg, &tgt)
    }

    #[test]
    fn rejects_audience_type_error() {
        let env = envelope_wrapping(&type_err_app_map("audience", cbor_int(42)));
        assert_eq!(parse_input(&env), Err(InputError::AudienceNotStringOrNull));
    }
    #[test]
    fn rejects_company_name_type_error() {
        let env = envelope_wrapping(&type_err_app_map("company_name", cbor_int(42)));
        assert_eq!(
            parse_input(&env),
            Err(InputError::CompanyNameNotStringOrNull)
        );
    }
    #[test]
    fn rejects_industry_type_error() {
        let env = envelope_wrapping(&type_err_app_map("industry", cbor_int(42)));
        assert_eq!(parse_input(&env), Err(InputError::IndustryNotStringOrNull));
    }
    #[test]
    fn rejects_notes_type_error() {
        let env = envelope_wrapping(&type_err_app_map("notes", cbor_int(42)));
        assert_eq!(parse_input(&env), Err(InputError::NotesNotStringOrNull));
    }
    #[test]
    fn rejects_profile_found_type_error_as_string() {
        let env = envelope_wrapping(&type_err_app_map("profile_found", cbor_text("true")));
        assert_eq!(parse_input(&env), Err(InputError::ProfileFoundNotBool));
    }
    #[test]
    fn rejects_profile_found_type_error_as_int() {
        let env = envelope_wrapping(&type_err_app_map("profile_found", cbor_int(1)));
        assert_eq!(parse_input(&env), Err(InputError::ProfileFoundNotBool));
    }
    #[test]
    fn rejects_segment_type_error() {
        let env = envelope_wrapping(&type_err_app_map("segment", cbor_int(42)));
        assert_eq!(parse_input(&env), Err(InputError::SegmentNotStringOrNull));
    }
    #[test]
    fn rejects_target_icp_type_error_as_null() {
        let env = envelope_wrapping(&type_err_app_map("target_icp", cbor_null()));
        assert_eq!(parse_input(&env), Err(InputError::TargetIcpNotString));
    }
    #[test]
    fn rejects_target_icp_type_error_as_int() {
        let env = envelope_wrapping(&type_err_app_map("target_icp", cbor_int(42)));
        assert_eq!(parse_input(&env), Err(InputError::TargetIcpNotString));
    }

    // =====================================================================
    // 7. CBOR string structure (IndefiniteLengthString)
    // =====================================================================

    #[test]
    fn rejects_target_icp_indefinite_length_string() {
        // CBOR 0x7F is the indef-length text string header; 0xFF closes.
        let indef_str = alloc::vec![0x7Fu8, 0x61, b'x', 0xFF];
        let env = envelope_wrapping(&type_err_app_map("target_icp", indef_str));
        assert_eq!(parse_input(&env), Err(InputError::IndefiniteLengthString));
    }

    #[test]
    fn rejects_notes_indefinite_length_string() {
        let indef_str = alloc::vec![0x7Fu8, 0x61, b'x', 0xFF];
        let env = envelope_wrapping(&type_err_app_map("notes", indef_str));
        assert_eq!(parse_input(&env), Err(InputError::IndefiniteLengthString));
    }

    // =====================================================================
    // 8. Per-field length errors
    // =====================================================================

    #[test]
    fn rejects_audience_too_long() {
        let overlong = "a".repeat(33);
        let env = envelope_wrapping(&type_err_app_map("audience", cbor_text(&overlong)));
        assert_eq!(parse_input(&env), Err(InputError::AudienceTooLong));
    }
    #[test]
    fn rejects_company_name_too_long() {
        let overlong = "a".repeat(257);
        let env = envelope_wrapping(&type_err_app_map("company_name", cbor_text(&overlong)));
        assert_eq!(parse_input(&env), Err(InputError::CompanyNameTooLong));
    }
    #[test]
    fn rejects_industry_too_long() {
        let overlong = "a".repeat(65);
        let env = envelope_wrapping(&type_err_app_map("industry", cbor_text(&overlong)));
        assert_eq!(parse_input(&env), Err(InputError::IndustryTooLong));
    }
    #[test]
    fn rejects_notes_too_long() {
        let overlong = "a".repeat(1025);
        let env = envelope_wrapping(&type_err_app_map("notes", cbor_text(&overlong)));
        assert_eq!(parse_input(&env), Err(InputError::NotesTooLong));
    }
    #[test]
    fn rejects_segment_too_long() {
        let overlong = "a".repeat(33);
        let env = envelope_wrapping(&type_err_app_map("segment", cbor_text(&overlong)));
        assert_eq!(parse_input(&env), Err(InputError::SegmentTooLong));
    }
    #[test]
    fn rejects_target_icp_empty() {
        let env = envelope_wrapping(&type_err_app_map("target_icp", cbor_text("")));
        assert_eq!(parse_input(&env), Err(InputError::TargetIcpEmpty));
    }
    #[test]
    fn rejects_target_icp_too_long() {
        let overlong = "a".repeat(1025);
        let env = envelope_wrapping(&type_err_app_map("target_icp", cbor_text(&overlong)));
        assert_eq!(parse_input(&env), Err(InputError::TargetIcpTooLong));
    }

    // =====================================================================
    // 9. Unicode code-point length vs UTF-8 byte length (D4)
    // =====================================================================

    #[test]
    fn accepts_notes_at_1024_codepoints_but_more_than_1024_bytes() {
        // Each 'e' with acute accent is 2 bytes in UTF-8 but 1 Unicode
        // code point. 1024 of them is 2048 bytes but 1024 chars().count().
        let s = "\u{00E9}".repeat(1024);
        assert_eq!(s.chars().count(), 1024);
        assert!(s.len() > 1024);
        let env = envelope_wrapping(&type_err_app_map("notes", cbor_text(&s)));
        let parsed = parse_input(&env).unwrap();
        assert_eq!(parsed.notes.unwrap().chars().count(), 1024);
    }

    #[test]
    fn rejects_notes_at_1025_codepoints() {
        let s = "\u{00E9}".repeat(1025);
        assert_eq!(s.chars().count(), 1025);
        let env = envelope_wrapping(&type_err_app_map("notes", cbor_text(&s)));
        assert_eq!(parse_input(&env), Err(InputError::NotesTooLong));
    }

    #[test]
    fn accepts_target_icp_at_1024_codepoints_but_more_than_1024_bytes() {
        let s = "\u{00E9}".repeat(1024);
        let env = envelope_wrapping(&type_err_app_map("target_icp", cbor_text(&s)));
        let parsed = parse_input(&env).unwrap();
        assert_eq!(parsed.target_icp.chars().count(), 1024);
    }

    // =====================================================================
    // 10. Conditional invariant (Pass 3)
    // =====================================================================

    #[test]
    fn rejects_found_profile_company_name_null() {
        let app = custom_app_map(
            &cbor_text("creators"),
            &cbor_null(),
            &cbor_text("b2b saas"),
            &cbor_null(),
            &cbor_bool(true),
            &cbor_text("smb"),
            &cbor_text("b2b saas smb creators"),
        );
        let env = envelope_wrapping(&app);
        assert_eq!(
            parse_input(&env),
            Err(InputError::FactsMustBeNonemptyWhenFound)
        );
    }

    #[test]
    fn rejects_found_profile_industry_null() {
        let app = custom_app_map(
            &cbor_text("creators"),
            &cbor_text("Example SaaS"),
            &cbor_null(),
            &cbor_null(),
            &cbor_bool(true),
            &cbor_text("smb"),
            &cbor_text("x"),
        );
        let env = envelope_wrapping(&app);
        assert_eq!(
            parse_input(&env),
            Err(InputError::FactsMustBeNonemptyWhenFound)
        );
    }

    #[test]
    fn rejects_found_profile_segment_null() {
        let app = custom_app_map(
            &cbor_text("creators"),
            &cbor_text("Example SaaS"),
            &cbor_text("b2b saas"),
            &cbor_null(),
            &cbor_bool(true),
            &cbor_null(),
            &cbor_text("x"),
        );
        let env = envelope_wrapping(&app);
        assert_eq!(
            parse_input(&env),
            Err(InputError::FactsMustBeNonemptyWhenFound)
        );
    }

    #[test]
    fn rejects_found_profile_audience_null() {
        let app = custom_app_map(
            &cbor_null(),
            &cbor_text("Example SaaS"),
            &cbor_text("b2b saas"),
            &cbor_null(),
            &cbor_bool(true),
            &cbor_text("smb"),
            &cbor_text("x"),
        );
        let env = envelope_wrapping(&app);
        assert_eq!(
            parse_input(&env),
            Err(InputError::FactsMustBeNonemptyWhenFound)
        );
    }

    #[test]
    fn rejects_found_profile_company_name_empty_string() {
        // Empty string is accepted by Pass 2 (base schema has no min on
        // the nullable field); Pass 3 rejects it under the then-branch.
        let app = custom_app_map(
            &cbor_text("creators"),
            &cbor_text(""),
            &cbor_text("b2b saas"),
            &cbor_null(),
            &cbor_bool(true),
            &cbor_text("smb"),
            &cbor_text("x"),
        );
        let env = envelope_wrapping(&app);
        assert_eq!(
            parse_input(&env),
            Err(InputError::FactsMustBeNonemptyWhenFound)
        );
    }

    #[test]
    fn rejects_unknown_profile_company_name_present() {
        let app = custom_app_map(
            &cbor_null(),
            &cbor_text("Example SaaS"),
            &cbor_null(),
            &cbor_null(),
            &cbor_bool(false),
            &cbor_null(),
            &cbor_text("x"),
        );
        let env = envelope_wrapping(&app);
        assert_eq!(
            parse_input(&env),
            Err(InputError::FactsMustBeNullWhenNotFound)
        );
    }

    #[test]
    fn rejects_unknown_profile_industry_present() {
        let app = custom_app_map(
            &cbor_null(),
            &cbor_null(),
            &cbor_text("b2b saas"),
            &cbor_null(),
            &cbor_bool(false),
            &cbor_null(),
            &cbor_text("x"),
        );
        let env = envelope_wrapping(&app);
        assert_eq!(
            parse_input(&env),
            Err(InputError::FactsMustBeNullWhenNotFound)
        );
    }

    #[test]
    fn rejects_unknown_profile_segment_present() {
        let app = custom_app_map(
            &cbor_null(),
            &cbor_null(),
            &cbor_null(),
            &cbor_null(),
            &cbor_bool(false),
            &cbor_text("smb"),
            &cbor_text("x"),
        );
        let env = envelope_wrapping(&app);
        assert_eq!(
            parse_input(&env),
            Err(InputError::FactsMustBeNullWhenNotFound)
        );
    }

    #[test]
    fn rejects_unknown_profile_audience_present() {
        let app = custom_app_map(
            &cbor_text("creators"),
            &cbor_null(),
            &cbor_null(),
            &cbor_null(),
            &cbor_bool(false),
            &cbor_null(),
            &cbor_text("x"),
        );
        let env = envelope_wrapping(&app);
        assert_eq!(
            parse_input(&env),
            Err(InputError::FactsMustBeNullWhenNotFound)
        );
    }

    #[test]
    fn rejects_unknown_profile_all_facts_set() {
        let app = custom_app_map(
            &cbor_text("creators"),
            &cbor_text("Example SaaS"),
            &cbor_text("b2b saas"),
            &cbor_null(),
            &cbor_bool(false),
            &cbor_text("smb"),
            &cbor_text("x"),
        );
        let env = envelope_wrapping(&app);
        assert_eq!(
            parse_input(&env),
            Err(InputError::FactsMustBeNullWhenNotFound)
        );
    }

    // =====================================================================
    // 11. Explicitly-requested parser-review cases
    // =====================================================================

    // T1 covered by `accepts_notes_at_1024_codepoints_but_more_than_1024_bytes`.
    // T2 covered by `unknown_field_wins_over_duplicate_field_regardless_of_wire_order`.
    // T3 covered by `unknown_field_wins_over_duplicate_reverse_order`.
    // T4 covered by `malformed_value_after_unknown_field_surfaces_malformed_cbor`.
    // T5 covered by `rejects_application_trailing_bytes`.

    #[test]
    fn profile_false_with_malformed_target_icp_rejects_in_parser_not_scorer() {
        // target_icp empty with profile_found=false still rejects at Pass 2
        // (TargetIcpEmpty), demonstrating input-validity-vs-qualification
        // separation (D6 + parser review architectural distinction).
        let app = custom_app_map(
            &cbor_null(),
            &cbor_null(),
            &cbor_null(),
            &cbor_null(),
            &cbor_bool(false),
            &cbor_null(),
            &cbor_text(""),
        );
        let env = envelope_wrapping(&app);
        assert_eq!(parse_input(&env), Err(InputError::TargetIcpEmpty));
    }

    #[test]
    fn profile_false_with_target_icp_wrong_type_rejects_in_parser() {
        let app = custom_app_map(
            &cbor_null(),
            &cbor_null(),
            &cbor_null(),
            &cbor_null(),
            &cbor_bool(false),
            &cbor_null(),
            &cbor_int(42),
        );
        let env = envelope_wrapping(&app);
        assert_eq!(parse_input(&env), Err(InputError::TargetIcpNotString));
    }

    // =====================================================================
    // 12. Order-independence smoke test
    // =====================================================================

    #[test]
    fn parses_fields_in_any_wire_order() {
        // Reverse-alphabetical wire order.
        let mut buf = Vec::new();
        {
            let mut enc = Encoder::new(&mut buf);
            enc.map(7).unwrap();
            enc.str("target_icp")
                .unwrap()
                .str("b2b saas smb creators")
                .unwrap();
            enc.str("segment").unwrap().str("smb").unwrap();
            enc.str("profile_found").unwrap().bool(true).unwrap();
            enc.str("notes").unwrap().null().unwrap();
            enc.str("industry").unwrap().str("b2b saas").unwrap();
            enc.str("company_name")
                .unwrap()
                .str("Example SaaS")
                .unwrap();
            enc.str("audience").unwrap().str("creators").unwrap();
        }
        let env = envelope_wrapping(&buf);
        let parsed = parse_input(&env).unwrap();
        assert_eq!(parsed.company_name, Some("Example SaaS"));
        assert_eq!(parsed.target_icp, "b2b saas smb creators");
    }
}
