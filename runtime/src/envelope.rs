use anyhow::{Context, Result};
use minicbor::encode::{self, Encoder, Write};

/// Trigger provenance for a brick invocation.
pub struct Trigger<'a> {
    pub source_node_id: &'a str,
    pub source_step: u64,
    pub edge_id: &'a str,
}

/// Root trigger sentinel for the entry node.
pub const ROOT_TRIGGER: Trigger<'static> = Trigger {
    source_node_id: "__root__",
    source_step: 0,
    edge_id: "__root__",
};

/// Build a CBOR envelope from JSON input.
///
/// Envelope shape (CBOR map, keys sorted alphabetically):
///   carry_state: null
///   ctx:         { graph_id, graph_version, node_id, session_id, step, trace_id, trigger }
///   graph_refs:  {}
///   input:       <the user's input value>
///
/// Input auto-detection:
///   - If JSON has top-level "input" key → use its value
///   - Else → wrap entire JSON as input
pub fn build_envelope(
    json_input: &serde_json::Value,
    graph_id: &str,
    graph_version: &str,
    node_id: &str,
    trace_id: &str,
    session_id: &str,
    step: u64,
    trigger: &Trigger<'_>,
) -> Result<Vec<u8>> {
    // Extract input value
    let input_value = if let Some(inner) = json_input.get("input") {
        inner
    } else {
        json_input
    };

    let mut buf = Vec::with_capacity(1024);
    let mut enc = Encoder::new(&mut buf);

    // Top-level map with 4 keys (sorted: carry_state, ctx, graph_refs, input)
    enc.map(4).context("encoding envelope map")?;

    // 1. carry_state = null
    enc.str("carry_state")
        .context("encoding 'carry_state' key")?;
    enc.null().context("encoding carry_state null")?;

    // 2. ctx (7 keys, sorted: graph_id, graph_version, node_id, session_id, step, trace_id, trigger)
    enc.str("ctx").context("encoding 'ctx' key")?;
    enc.map(7).context("encoding ctx map")?;
    enc.str("graph_id").context("encoding ctx.graph_id key")?;
    enc.str(graph_id).context("encoding ctx.graph_id value")?;
    enc.str("graph_version")
        .context("encoding ctx.graph_version key")?;
    enc.str(graph_version)
        .context("encoding ctx.graph_version value")?;
    enc.str("node_id").context("encoding ctx.node_id key")?;
    enc.str(node_id).context("encoding ctx.node_id value")?;
    enc.str("session_id")
        .context("encoding ctx.session_id key")?;
    enc.str(session_id)
        .context("encoding ctx.session_id value")?;
    enc.str("step").context("encoding ctx.step key")?;
    enc.u64(step).context("encoding ctx.step value")?;
    enc.str("trace_id").context("encoding ctx.trace_id key")?;
    enc.str(trace_id).context("encoding ctx.trace_id value")?;
    enc.str("trigger").context("encoding ctx.trigger key")?;
    // trigger map (3 keys, sorted: edge_id, source_node_id, source_step)
    enc.map(3).context("encoding trigger map")?;
    enc.str("edge_id").context("encoding trigger.edge_id key")?;
    enc.str(trigger.edge_id)
        .context("encoding trigger.edge_id value")?;
    enc.str("source_node_id")
        .context("encoding trigger.source_node_id key")?;
    enc.str(trigger.source_node_id)
        .context("encoding trigger.source_node_id value")?;
    enc.str("source_step")
        .context("encoding trigger.source_step key")?;
    enc.u64(trigger.source_step)
        .context("encoding trigger.source_step value")?;

    // 3. graph_refs = {}
    enc.str("graph_refs").context("encoding 'graph_refs' key")?;
    enc.map(0).context("encoding empty graph_refs")?;

    // 4. input
    enc.str("input").context("encoding 'input' key")?;
    encode_json_value(&mut enc, input_value).context("encoding input value")?;

    Ok(buf)
}

/// Recursively encode a serde_json::Value as CBOR.
/// Object keys are sorted alphabetically for cross-runtime determinism.
fn encode_json_value<W: Write>(
    enc: &mut Encoder<W>,
    val: &serde_json::Value,
) -> Result<(), encode::Error<W::Error>> {
    match val {
        serde_json::Value::Null => {
            enc.null()?;
        }
        serde_json::Value::Bool(b) => {
            enc.bool(*b)?;
        }
        serde_json::Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                enc.i64(i)?;
            } else if let Some(u) = n.as_u64() {
                enc.u64(u)?;
            } else if let Some(f) = n.as_f64() {
                enc.f64(f)?;
            } else {
                return Err(encode::Error::message("unsupported JSON number"));
            }
        }
        serde_json::Value::String(s) => {
            enc.str(s)?;
        }
        serde_json::Value::Array(arr) => {
            enc.array(arr.len() as u64)?;
            for item in arr {
                encode_json_value(enc, item)?;
            }
        }
        serde_json::Value::Object(map) => {
            enc.map(map.len() as u64)?;
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            for k in keys {
                enc.str(k)?;
                encode_json_value(enc, &map[k])?;
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn envelope_deterministic_regardless_of_key_order() {
        let json_a: serde_json::Value =
            serde_json::from_str(r#"{"alpha": 1, "beta": "two", "gamma": [3, 4]}"#).unwrap();
        let json_b: serde_json::Value =
            serde_json::from_str(r#"{"gamma": [3, 4], "alpha": 1, "beta": "two"}"#).unwrap();

        let env_a =
            build_envelope(&json_a, "g1", "v1", "n1", "t1", "s1", 0, &ROOT_TRIGGER).unwrap();
        let env_b =
            build_envelope(&json_b, "g1", "v1", "n1", "t1", "s1", 0, &ROOT_TRIGGER).unwrap();

        assert_eq!(
            env_a, env_b,
            "envelopes must be byte-identical regardless of JSON key order"
        );
    }

    #[test]
    fn envelope_auto_wraps_raw_input() {
        let raw: serde_json::Value = serde_json::from_str(r#"{"text": "hello"}"#).unwrap();
        let wrapped: serde_json::Value =
            serde_json::from_str(r#"{"input": {"text": "hello"}}"#).unwrap();

        let env_raw = build_envelope(&raw, "g1", "v1", "n1", "t1", "s1", 0, &ROOT_TRIGGER).unwrap();
        let env_wrapped =
            build_envelope(&wrapped, "g1", "v1", "n1", "t1", "s1", 0, &ROOT_TRIGGER).unwrap();

        assert_eq!(
            env_raw, env_wrapped,
            "raw and wrapped inputs must produce identical envelopes"
        );
    }
}
