use std::collections::HashMap;

use crate::result::CborValue;

/// Resolve a dot-path from a CborValue map.
/// e.g., resolve_path(value, "output.label") traverses into nested maps.
/// Returns None if any segment is missing or the value is not a map at an intermediate step.
/// Empty path returns the value itself.
pub fn resolve_path(value: &CborValue, path: &str) -> Option<CborValue> {
    if path.is_empty() {
        return Some(value.clone());
    }

    let segments: Vec<&str> = path.split('.').collect();
    let mut current = value.clone();

    for segment in &segments {
        match current {
            CborValue::Map(pairs) => {
                let found = pairs.into_iter().find(|(k, _)| {
                    matches!(k, CborValue::Text(s) if s == segment)
                });
                match found {
                    Some((_, v)) => current = v,
                    None => return None,
                }
            }
            _ => return None,
        }
    }

    Some(current)
}

/// Build a nested CborValue map from a dot-path and a value.
/// e.g., set_path("input.sentiment", val) → Map { "input": Map { "sentiment": val } }
/// Empty path returns the value itself.
pub fn set_path(path: &str, value: CborValue) -> CborValue {
    if path.is_empty() {
        return value;
    }

    let segments: Vec<&str> = path.split('.').collect();
    let mut result = value;

    // Build inside-out: wrap the value in nested single-key maps from right to left
    for segment in segments.into_iter().rev() {
        result = CborValue::Map(vec![(
            CborValue::Text(segment.to_string()),
            result,
        )]);
    }

    result
}

/// Merge two CborValue maps deeply. Keys from `overlay` overwrite keys in `base`.
/// Preserves stable key order: existing keys stay in place, new keys appended.
/// Non-map values: overlay wins.
pub fn merge_maps(base: CborValue, overlay: CborValue) -> CborValue {
    match (base, overlay) {
        (CborValue::Map(mut base_pairs), CborValue::Map(overlay_pairs)) => {
            // Index existing text keys → position
            let mut idx: HashMap<String, usize> = HashMap::new();
            for (i, (k, _)) in base_pairs.iter().enumerate() {
                if let CborValue::Text(s) = k {
                    idx.insert(s.clone(), i);
                }
            }

            for (ok, ov) in overlay_pairs {
                if let CborValue::Text(ref ks) = ok {
                    if let Some(&i) = idx.get(ks) {
                        let old = std::mem::replace(
                            &mut base_pairs[i],
                            (CborValue::Null, CborValue::Null),
                        );
                        let (bk, bv) = old;
                        let merged = merge_maps(bv, ov);
                        base_pairs[i] = (bk, merged);
                    } else {
                        idx.insert(ks.clone(), base_pairs.len());
                        base_pairs.push((ok, ov));
                    }
                } else {
                    // Non-text keys: just append (shouldn't happen for spec-shaped maps)
                    base_pairs.push((ok, ov));
                }
            }

            CborValue::Map(base_pairs)
        }
        (_, overlay) => overlay,
    }
}

/// Convert a CborValue to a serde_json::Value for final output.
pub fn cbor_to_json(value: &CborValue) -> serde_json::Value {
    match value {
        CborValue::Null => serde_json::Value::Null,
        CborValue::Bool(b) => serde_json::Value::Bool(*b),
        CborValue::Integer(n) => serde_json::json!(n),
        CborValue::Float(f) => serde_json::json!(f),
        CborValue::Text(s) => serde_json::Value::String(s.clone()),
        CborValue::Bytes(b) => serde_json::Value::String(hex::encode(b)),
        CborValue::Array(items) => {
            serde_json::Value::Array(items.iter().map(cbor_to_json).collect())
        }
        CborValue::Map(pairs) => {
            let mut map = serde_json::Map::new();
            for (k, v) in pairs {
                let key = match k {
                    CborValue::Text(s) => s.clone(),
                    other => format!("{other:?}"),
                };
                map.insert(key, cbor_to_json(v));
            }
            serde_json::Value::Object(map)
        }
    }
}

/// Extract output.confidence as Option<f64> from a BrickResult's output.
pub fn extract_confidence(value: &CborValue) -> Option<f64> {
    match resolve_path(value, "confidence") {
        Some(CborValue::Float(f)) => Some(f),
        Some(CborValue::Integer(n)) => Some(n as f64),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn text(s: &str) -> CborValue { CborValue::Text(s.to_string()) }
    fn int(n: i64) -> CborValue { CborValue::Integer(n) }

    fn sample_map() -> CborValue {
        CborValue::Map(vec![
            (text("label"), text("positive")),
            (text("confidence"), CborValue::Float(0.95)),
            (text("nested"), CborValue::Map(vec![
                (text("deep"), int(42)),
            ])),
        ])
    }

    #[test]
    fn resolve_top_level() {
        let map = sample_map();
        let val = resolve_path(&map, "label").unwrap();
        assert!(matches!(val, CborValue::Text(s) if s == "positive"));
    }

    #[test]
    fn resolve_nested() {
        let map = sample_map();
        let val = resolve_path(&map, "nested.deep").unwrap();
        assert!(matches!(val, CborValue::Integer(42)));
    }

    #[test]
    fn resolve_missing() {
        let map = sample_map();
        assert!(resolve_path(&map, "nonexistent").is_none());
        assert!(resolve_path(&map, "nested.missing").is_none());
    }

    #[test]
    fn resolve_empty_path() {
        let map = sample_map();
        let val = resolve_path(&map, "").unwrap();
        assert!(matches!(val, CborValue::Map(_)));
    }

    #[test]
    fn set_single_segment() {
        let result = set_path("label", text("positive"));
        let resolved = resolve_path(&result, "label").unwrap();
        assert!(matches!(resolved, CborValue::Text(s) if s == "positive"));
    }

    #[test]
    fn set_multi_segment() {
        let result = set_path("input.sentiment", text("positive"));
        let resolved = resolve_path(&result, "input.sentiment").unwrap();
        assert!(matches!(resolved, CborValue::Text(s) if s == "positive"));
    }

    #[test]
    fn set_empty_path() {
        let val = text("hello");
        let result = set_path("", val);
        assert!(matches!(result, CborValue::Text(s) if s == "hello"));
    }

    #[test]
    fn merge_disjoint() {
        let a = set_path("x", int(1));
        let b = set_path("y", int(2));
        let merged = merge_maps(a, b);
        assert!(matches!(resolve_path(&merged, "x"), Some(CborValue::Integer(1))));
        assert!(matches!(resolve_path(&merged, "y"), Some(CborValue::Integer(2))));
    }

    #[test]
    fn merge_deep() {
        let a = set_path("input.x", int(1));
        let b = set_path("input.y", int(2));
        let merged = merge_maps(a, b);
        assert!(matches!(resolve_path(&merged, "input.x"), Some(CborValue::Integer(1))));
        assert!(matches!(resolve_path(&merged, "input.y"), Some(CborValue::Integer(2))));
    }

    #[test]
    fn merge_preserves_order() {
        let base = CborValue::Map(vec![
            (text("a"), int(1)),
            (text("b"), int(2)),
            (text("c"), int(3)),
        ]);
        let overlay = CborValue::Map(vec![
            (text("b"), int(20)),
            (text("d"), int(4)),
        ]);
        let merged = merge_maps(base, overlay);
        if let CborValue::Map(pairs) = &merged {
            let keys: Vec<&str> = pairs.iter().map(|(k, _)| {
                if let CborValue::Text(s) = k { s.as_str() } else { "" }
            }).collect();
            assert_eq!(keys, vec!["a", "b", "c", "d"]);
            assert!(matches!(resolve_path(&merged, "b"), Some(CborValue::Integer(20))));
        } else {
            panic!("expected map");
        }
    }

    #[test]
    fn extract_confidence_float() {
        let map = sample_map();
        assert_eq!(extract_confidence(&map), Some(0.95));
    }

    #[test]
    fn extract_confidence_missing() {
        let map = CborValue::Map(vec![(text("label"), text("positive"))]);
        assert_eq!(extract_confidence(&map), None);
    }

    #[test]
    fn cbor_to_json_roundtrip() {
        let cbor = sample_map();
        let json = cbor_to_json(&cbor);
        assert_eq!(json["label"], "positive");
        assert_eq!(json["confidence"], 0.95);
        assert_eq!(json["nested"]["deep"], 42);
    }
}
