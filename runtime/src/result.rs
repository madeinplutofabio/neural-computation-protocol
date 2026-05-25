use std::collections::HashMap;

use anyhow::{bail, Context, Result};

/// Maximum definite length for CBOR arrays/maps before we reject.
/// Prevents huge allocation from a malicious/buggy brick before max_output_bytes kicks in.
const MAX_COLLECTION_LEN: u64 = 1_000_000;

/// Decoded brick result after boundary validation.
#[derive(Debug, Clone)]
pub enum BrickResult {
    Success {
        output: CborValue,
    },
    LowConfidence {
        output: CborValue,
        error: ErrorObject,
    },
    Failure {
        error: ErrorObject,
    },
}

/// Decoded error object from a brick result.
#[derive(Debug, Clone)]
pub struct ErrorObject {
    pub error_class: String,
    pub message: String,
    #[allow(dead_code)]
    pub retry_advice: Option<String>,
    #[allow(dead_code)]
    pub severity: Option<String>,
}

/// Minimal CBOR value representation for runtime use.
#[derive(Debug, Clone)]
pub enum CborValue {
    Null,
    Bool(bool),
    Integer(i64),
    Float(f64),
    Text(String),
    Bytes(Vec<u8>),
    Array(Vec<CborValue>),
    Map(Vec<(CborValue, CborValue)>),
}

impl BrickResult {
    /// Get the result type as a string.
    pub fn result_type(&self) -> &str {
        match self {
            Self::Success { .. } => "Success",
            Self::LowConfidence { .. } => "LowConfidence",
            Self::Failure { .. } => "Failure",
        }
    }

    /// Get the output value, if present.
    pub fn output(&self) -> Option<&CborValue> {
        match self {
            Self::Success { output } | Self::LowConfidence { output, .. } => Some(output),
            Self::Failure { .. } => None,
        }
    }

    /// Get the error object, if present.
    pub fn error(&self) -> Option<&ErrorObject> {
        match self {
            Self::LowConfidence { error, .. } | Self::Failure { error } => Some(error),
            Self::Success { .. } => None,
        }
    }
}

/// Decode raw CBOR result bytes and validate §9.2 structural boundaries.
pub fn decode_result(cbor_bytes: &[u8]) -> Result<BrickResult> {
    let mut decoder = minicbor::Decoder::new(cbor_bytes);

    // Top-level must be a definite-length map
    let map_len = match decoder.map() {
        Ok(Some(len)) => len,
        Ok(None) => bail!("result is an indefinite-length map (must be definite)"),
        Err(e) => bail!("result is not a valid CBOR map: {e}"),
    };

    if map_len > MAX_COLLECTION_LEN {
        bail!("result map has {map_len} entries (max {MAX_COLLECTION_LEN})");
    }

    // Collect all top-level key-value pairs, rejecting duplicates
    let mut fields: HashMap<String, CborValue> = HashMap::new();
    for _ in 0..map_len {
        let key = decode_text(&mut decoder).context("result map key must be a text string")?;
        let value = decode_value(&mut decoder).context("decoding result map value")?;
        if fields.insert(key.clone(), value).is_some() {
            bail!("duplicate top-level key in result map: '{key}'");
        }
    }

    // Extract discriminant
    let type_val = fields
        .get("type")
        .ok_or_else(|| anyhow::anyhow!("result missing 'type' discriminant field"))?;
    let type_str = match type_val {
        CborValue::Text(s) => s.as_str(),
        _ => bail!("result 'type' field must be a text string"),
    };

    match type_str {
        "Success" => validate_success(&fields),
        "LowConfidence" => validate_low_confidence(&fields),
        "Failure" => validate_failure(&fields),
        other => {
            bail!("unknown result type '{other}' (expected Success, LowConfidence, or Failure)")
        }
    }
}

fn validate_success(fields: &HashMap<String, CborValue>) -> Result<BrickResult> {
    // MUST have output
    let output = fields
        .get("output")
        .ok_or_else(|| anyhow::anyhow!("Success result missing 'output' field"))?
        .clone();

    // MUST NOT have error
    if fields.contains_key("error") {
        bail!("Success result MUST NOT have 'error' field");
    }

    // MUST NOT have carry_state_side_effects
    if fields.contains_key("carry_state_side_effects") {
        bail!("Success result MUST NOT have 'carry_state_side_effects' field");
    }

    // Phase 2: carry_state_next must be null or absent (carry_state_class=none)
    if let Some(v) = fields.get("carry_state_next") {
        if !matches!(v, CborValue::Null) {
            bail!("carry_state_next must be null/absent in Phase 2 (carry_state_class=none)");
        }
    }

    Ok(BrickResult::Success { output })
}

fn validate_low_confidence(fields: &HashMap<String, CborValue>) -> Result<BrickResult> {
    // MUST have output
    let output = fields
        .get("output")
        .ok_or_else(|| anyhow::anyhow!("LowConfidence result missing 'output' field"))?
        .clone();

    // MUST have error
    let error_val = fields
        .get("error")
        .ok_or_else(|| anyhow::anyhow!("LowConfidence result missing 'error' field"))?;
    let error = parse_error_object(error_val).context("parsing LowConfidence error object")?;

    // error.error_class MUST be LOW_CONFIDENCE
    if error.error_class != "LOW_CONFIDENCE" {
        bail!(
            "LowConfidence result error.error_class must be 'LOW_CONFIDENCE', got '{}'",
            error.error_class
        );
    }

    // MUST NOT have carry_state_side_effects
    if fields.contains_key("carry_state_side_effects") {
        bail!("LowConfidence result MUST NOT have 'carry_state_side_effects' field");
    }

    // Phase 2: carry_state_next must be null or absent (carry_state_class=none)
    if let Some(v) = fields.get("carry_state_next") {
        if !matches!(v, CborValue::Null) {
            bail!("carry_state_next must be null/absent in Phase 2 (carry_state_class=none)");
        }
    }

    Ok(BrickResult::LowConfidence { output, error })
}

fn validate_failure(fields: &HashMap<String, CborValue>) -> Result<BrickResult> {
    // MUST have error
    let error_val = fields
        .get("error")
        .ok_or_else(|| anyhow::anyhow!("Failure result missing 'error' field"))?;
    let error = parse_error_object(error_val).context("parsing Failure error object")?;

    // error.error_class MUST NOT be LOW_CONFIDENCE
    if error.error_class == "LOW_CONFIDENCE" {
        bail!("Failure result error.error_class MUST NOT be 'LOW_CONFIDENCE'");
    }

    // MUST NOT have output
    if fields.contains_key("output") {
        bail!("Failure result MUST NOT have 'output' field");
    }

    // MUST NOT have carry_state_next
    if fields.contains_key("carry_state_next") {
        bail!("Failure result MUST NOT have 'carry_state_next' field");
    }

    // carry_state_side_effects: manifest-dependent, enforced in orchestration layer.

    Ok(BrickResult::Failure { error })
}

/// Parse an error object from a CborValue (must be a map with error_class + message).
fn parse_error_object(val: &CborValue) -> Result<ErrorObject> {
    let map = match val {
        CborValue::Map(pairs) => pairs,
        _ => bail!("error field must be a CBOR map"),
    };

    let mut error_class: Option<String> = None;
    let mut message: Option<String> = None;
    let mut retry_advice: Option<String> = None;
    let mut severity: Option<String> = None;

    for (k, v) in map {
        let key = match k {
            CborValue::Text(s) => s.as_str(),
            _ => bail!("error map key must be a text string"),
        };
        match key {
            "error_class" => {
                if error_class.is_some() {
                    bail!("duplicate key 'error_class' in error object");
                }
                error_class = Some(extract_text(v).context("error.error_class must be text")?);
            }
            "message" => {
                if message.is_some() {
                    bail!("duplicate key 'message' in error object");
                }
                message = Some(extract_text(v).context("error.message must be text")?);
            }
            "retry_advice" => {
                if retry_advice.is_some() {
                    bail!("duplicate key 'retry_advice' in error object");
                }
                retry_advice = Some(extract_text(v).context("error.retry_advice must be text")?);
            }
            "severity" => {
                if severity.is_some() {
                    bail!("duplicate key 'severity' in error object");
                }
                severity = Some(extract_text(v).context("error.severity must be text")?);
            }
            _ => {} // ignore unknown fields for forward compatibility
        }
    }

    let error_class =
        error_class.ok_or_else(|| anyhow::anyhow!("error object missing 'error_class' field"))?;
    let message = message.ok_or_else(|| anyhow::anyhow!("error object missing 'message' field"))?;

    Ok(ErrorObject {
        error_class,
        message,
        retry_advice,
        severity,
    })
}

fn extract_text(val: &CborValue) -> Result<String> {
    match val {
        CborValue::Text(s) => Ok(s.clone()),
        _ => bail!("expected text string"),
    }
}

// ── CBOR Decoding ───────────────────────────────────────────────────

fn decode_text(d: &mut minicbor::Decoder<'_>) -> Result<String> {
    d.str()
        .map(|s| s.to_string())
        .map_err(|e| anyhow::anyhow!("expected CBOR text string: {e}"))
}

fn decode_value(d: &mut minicbor::Decoder<'_>) -> Result<CborValue> {
    use minicbor::data::Type;

    match d
        .datatype()
        .map_err(|e| anyhow::anyhow!("cannot peek CBOR type: {e}"))?
    {
        Type::Null => {
            d.null()
                .map_err(|e| anyhow::anyhow!("decoding null: {e}"))?;
            Ok(CborValue::Null)
        }
        Type::Undefined => {
            d.undefined()
                .map_err(|e| anyhow::anyhow!("consuming undefined: {e}"))?;
            bail!("CBOR undefined is not allowed in NCP results");
        }
        Type::Bool => {
            let b = d
                .bool()
                .map_err(|e| anyhow::anyhow!("decoding bool: {e}"))?;
            Ok(CborValue::Bool(b))
        }
        Type::U8 | Type::U16 | Type::U32 | Type::U64 => {
            let n = d.u64().map_err(|e| anyhow::anyhow!("decoding uint: {e}"))?;
            if n > i64::MAX as u64 {
                bail!("CBOR uint too large for i64: {n}");
            }
            Ok(CborValue::Integer(n as i64))
        }
        Type::I8 | Type::I16 | Type::I32 | Type::I64 => {
            let n = d.i64().map_err(|e| anyhow::anyhow!("decoding int: {e}"))?;
            Ok(CborValue::Integer(n))
        }
        Type::F16 | Type::F32 | Type::F64 => {
            let f = d
                .f64()
                .map_err(|e| anyhow::anyhow!("decoding float: {e}"))?;
            Ok(CborValue::Float(f))
        }
        Type::String => {
            let s = decode_text(d)?;
            Ok(CborValue::Text(s))
        }
        Type::Bytes => {
            let b = d
                .bytes()
                .map_err(|e| anyhow::anyhow!("decoding bytes: {e}"))?
                .to_vec();
            Ok(CborValue::Bytes(b))
        }
        Type::Array => {
            let len = d
                .array()
                .map_err(|e| anyhow::anyhow!("decoding array: {e}"))?
                .ok_or_else(|| anyhow::anyhow!("indefinite-length arrays not supported"))?;
            if len > MAX_COLLECTION_LEN {
                bail!("CBOR array has {len} elements (max {MAX_COLLECTION_LEN})");
            }
            let mut items = Vec::with_capacity(len as usize);
            for _ in 0..len {
                items.push(decode_value(d)?);
            }
            Ok(CborValue::Array(items))
        }
        Type::Map => {
            let len = d
                .map()
                .map_err(|e| anyhow::anyhow!("decoding map: {e}"))?
                .ok_or_else(|| anyhow::anyhow!("indefinite-length maps not supported"))?;
            if len > MAX_COLLECTION_LEN {
                bail!("CBOR map has {len} entries (max {MAX_COLLECTION_LEN})");
            }
            let mut pairs = Vec::with_capacity(len as usize);
            for _ in 0..len {
                let k = decode_value(d)?;
                let v = decode_value(d)?;
                pairs.push((k, v));
            }
            Ok(CborValue::Map(pairs))
        }
        Type::Tag => {
            let tag = d.tag().map_err(|e| anyhow::anyhow!("decoding tag: {e}"))?;
            bail!("CBOR tags are not supported in Phase 2 results (tag={tag:?})");
        }
        other => bail!("unsupported CBOR type: {other:?}"),
    }
}

// ── Trap Detection ──────────────────────────────────────────────────

/// Create a Failure result for a WASM trap (invoke error).
pub fn trap_failure(error_class: &str, message: String) -> BrickResult {
    BrickResult::Failure {
        error: ErrorObject {
            error_class: error_class.to_string(),
            message,
            retry_advice: None,
            severity: None,
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use minicbor::encode::Encoder;

    /// Helper: encode a result map to CBOR bytes.
    fn encode_result(fields: &[(&str, EncodableValue)]) -> Vec<u8> {
        let mut buf = Vec::new();
        let mut enc = Encoder::new(&mut buf);
        enc.map(fields.len() as u64).unwrap();
        for (key, val) in fields {
            enc.str(key).unwrap();
            encode_test_value(&mut enc, val);
        }
        buf
    }

    #[allow(dead_code)]
    enum EncodableValue {
        Text(String),
        Int(i64),
        Float(f64),
        Null,
        Map(Vec<(String, EncodableValue)>),
    }

    fn encode_test_value(enc: &mut Encoder<&mut Vec<u8>>, val: &EncodableValue) {
        match val {
            EncodableValue::Text(s) => {
                enc.str(s).unwrap();
            }
            EncodableValue::Int(n) => {
                enc.i64(*n).unwrap();
            }
            EncodableValue::Float(f) => {
                enc.f64(*f).unwrap();
            }
            EncodableValue::Null => {
                enc.null().unwrap();
            }
            EncodableValue::Map(pairs) => {
                enc.map(pairs.len() as u64).unwrap();
                for (k, v) in pairs {
                    enc.str(k).unwrap();
                    encode_test_value(enc, v);
                }
            }
        }
    }

    fn text(s: &str) -> EncodableValue {
        EncodableValue::Text(s.to_string())
    }
    fn output_map() -> EncodableValue {
        EncodableValue::Map(vec![
            ("label".into(), text("positive")),
            ("confidence".into(), EncodableValue::Float(0.95)),
        ])
    }
    fn error_obj(class: &str) -> EncodableValue {
        EncodableValue::Map(vec![
            ("error_class".into(), text(class)),
            ("message".into(), text("something went wrong")),
        ])
    }

    // ── Valid variants ──────────────────────────────────────────────

    #[test]
    fn valid_success() {
        let bytes = encode_result(&[("type", text("Success")), ("output", output_map())]);
        let result = decode_result(&bytes).unwrap();
        assert_eq!(result.result_type(), "Success");
        assert!(result.output().is_some());
        assert!(result.error().is_none());
    }

    #[test]
    fn valid_low_confidence() {
        let bytes = encode_result(&[
            ("type", text("LowConfidence")),
            ("output", output_map()),
            ("error", error_obj("LOW_CONFIDENCE")),
        ]);
        let result = decode_result(&bytes).unwrap();
        assert_eq!(result.result_type(), "LowConfidence");
        assert!(result.output().is_some());
        assert_eq!(result.error().unwrap().error_class, "LOW_CONFIDENCE");
    }

    #[test]
    fn valid_failure() {
        let bytes = encode_result(&[
            ("type", text("Failure")),
            ("error", error_obj("COMPUTATION_ERROR")),
        ]);
        let result = decode_result(&bytes).unwrap();
        assert_eq!(result.result_type(), "Failure");
        assert!(result.output().is_none());
        assert_eq!(result.error().unwrap().error_class, "COMPUTATION_ERROR");
    }

    #[test]
    fn valid_success_with_null_carry_state_next() {
        let bytes = encode_result(&[
            ("type", text("Success")),
            ("output", output_map()),
            ("carry_state_next", EncodableValue::Null),
        ]);
        let result = decode_result(&bytes).unwrap();
        assert_eq!(result.result_type(), "Success");
    }

    // ── Invalid variants ────────────────────────────────────────────

    #[test]
    fn invalid_success_with_error() {
        let bytes = encode_result(&[
            ("type", text("Success")),
            ("output", output_map()),
            ("error", error_obj("LOW_CONFIDENCE")),
        ]);
        let err = decode_result(&bytes).unwrap_err();
        assert!(err.to_string().contains("MUST NOT have 'error'"));
    }

    #[test]
    fn invalid_failure_with_output() {
        let bytes = encode_result(&[
            ("type", text("Failure")),
            ("error", error_obj("COMPUTATION_ERROR")),
            ("output", output_map()),
        ]);
        let err = decode_result(&bytes).unwrap_err();
        assert!(err.to_string().contains("MUST NOT have 'output'"));
    }

    #[test]
    fn invalid_low_confidence_without_error() {
        let bytes = encode_result(&[("type", text("LowConfidence")), ("output", output_map())]);
        let err = decode_result(&bytes).unwrap_err();
        assert!(err.to_string().contains("missing 'error'"));
    }

    #[test]
    fn invalid_low_confidence_wrong_error_class() {
        let bytes = encode_result(&[
            ("type", text("LowConfidence")),
            ("output", output_map()),
            ("error", error_obj("COMPUTATION_ERROR")),
        ]);
        let err = decode_result(&bytes).unwrap_err();
        assert!(err.to_string().contains("must be 'LOW_CONFIDENCE'"));
    }

    #[test]
    fn invalid_missing_type() {
        let bytes = encode_result(&[("output", output_map())]);
        let err = decode_result(&bytes).unwrap_err();
        assert!(err.to_string().contains("missing 'type'"));
    }

    #[test]
    fn invalid_unknown_type() {
        let bytes = encode_result(&[("type", text("Unknown")), ("output", output_map())]);
        let err = decode_result(&bytes).unwrap_err();
        assert!(err.to_string().contains("unknown result type"));
    }

    #[test]
    fn invalid_error_missing_message() {
        let error_no_msg =
            EncodableValue::Map(vec![("error_class".into(), text("COMPUTATION_ERROR"))]);
        let bytes = encode_result(&[("type", text("Failure")), ("error", error_no_msg)]);
        let err = decode_result(&bytes).unwrap_err();
        assert!(
            err.chain()
                .any(|c| c.to_string().contains("missing 'message'")),
            "expected cause not found in error chain: {err:?}"
        );
    }

    #[test]
    fn invalid_failure_with_low_confidence_class() {
        let bytes = encode_result(&[
            ("type", text("Failure")),
            ("error", error_obj("LOW_CONFIDENCE")),
        ]);
        let err = decode_result(&bytes).unwrap_err();
        assert!(err.to_string().contains("MUST NOT be 'LOW_CONFIDENCE'"));
    }

    #[test]
    fn invalid_duplicate_top_level_key() {
        let mut buf = Vec::new();
        let mut enc = Encoder::new(&mut buf);
        enc.map(3).unwrap();
        enc.str("type").unwrap();
        enc.str("Success").unwrap();
        enc.str("output").unwrap();
        enc.str("hello").unwrap();
        enc.str("type").unwrap();
        enc.str("Failure").unwrap();
        let err = decode_result(&buf).unwrap_err();
        assert!(err.to_string().contains("duplicate top-level key"));
    }

    #[test]
    fn invalid_failure_with_carry_state_next() {
        let bytes = encode_result(&[
            ("type", text("Failure")),
            ("error", error_obj("COMPUTATION_ERROR")),
            ("carry_state_next", EncodableValue::Null),
        ]);
        let err = decode_result(&bytes).unwrap_err();
        assert!(err.to_string().contains("MUST NOT have 'carry_state_next'"));
    }

    #[test]
    fn invalid_success_with_non_null_carry_state_next() {
        let bytes = encode_result(&[
            ("type", text("Success")),
            ("output", output_map()),
            ("carry_state_next", text("some_state")),
        ]);
        let err = decode_result(&bytes).unwrap_err();
        assert!(err.to_string().contains("carry_state_next must be null"));
    }

    #[test]
    fn invalid_error_duplicate_key() {
        let mut buf = Vec::new();
        let mut enc = Encoder::new(&mut buf);
        enc.map(2).unwrap();
        enc.str("type").unwrap();
        enc.str("Failure").unwrap();
        enc.str("error").unwrap();
        enc.map(3).unwrap();
        enc.str("error_class").unwrap();
        enc.str("COMPUTATION_ERROR").unwrap();
        enc.str("error_class").unwrap();
        enc.str("LOW_CONFIDENCE").unwrap();
        enc.str("message").unwrap();
        enc.str("oops").unwrap();
        let err = decode_result(&buf).unwrap_err();
        assert!(
            err.chain()
                .any(|c| c.to_string().contains("duplicate key 'error_class'")),
            "expected cause not found in error chain: {err:?}"
        );
    }
}
