use std::io::Write;

use sha2::{Digest, Sha256};

use crate::result::BrickResult;

/// Trace schema version — included in runtime_info and every invoke record.
const TRACE_SCHEMA_VERSION: &str = "ncp-trace-0.1";

/// Trace writer — emits JSON Lines to a configurable destination.
pub struct TraceWriter {
    dest: Box<dyn Write>,
}

impl TraceWriter {
    /// Create a trace writer that writes to stderr.
    pub fn stderr() -> Self {
        Self {
            dest: Box::new(std::io::stderr()),
        }
    }

    /// Create a trace writer that writes to a file.
    pub fn file(path: &std::path::Path) -> std::io::Result<Self> {
        let file = std::fs::File::create(path)?;
        Ok(Self {
            dest: Box::new(std::io::BufWriter::new(file)),
        })
    }

    /// Emit the runtime_info header record (first line of trace).
    pub fn emit_runtime_info(
        &mut self,
        runtime_version: &str,
        wasmtime_version: &str,
        timestamp: &str,
    ) {
        let record = serde_json::json!({
            "type": "runtime_info",
            "trace_schema_version": TRACE_SCHEMA_VERSION,
            "runtime_version": runtime_version,
            "wasmtime_version": wasmtime_version,
            "timestamp": timestamp,
        });
        self.write_line(&record);
    }

    /// Emit a per-invoke trace record.
    pub fn emit_invoke(
        &mut self,
        params: &InvokeTraceParams<'_>,
        result: &BrickResult,
        result_bytes: Option<&[u8]>,
        latency_ms: f64,
        timestamp: &str,
    ) {
        let input_hash = sha256_prefixed(params.envelope_bytes);
        let output_hash = result_bytes.map(|b| sha256_prefixed(b));
        let result_len = result_bytes.map(|b| b.len());

        let mut record = serde_json::json!({
            "type": "invoke",
            "trace_schema_version": TRACE_SCHEMA_VERSION,
            "timestamp": timestamp,
            "trace_id": params.trace_id,
            "session_id": params.session_id,
            "step": params.step,
            "graph_id": params.graph_id,
            "graph_version": params.graph_version,
            "brick_id": params.brick_id,
            "brick_version": params.brick_version,
            "bundle_digest": params.bundle_digest,
            "node_id": params.node_id,
            "trigger": {
                "source_node_id": params.trigger_source_node_id,
                "source_step": params.trigger_source_step,
                "edge_id": params.trigger_edge_id,
                "routing_reason": params.trigger_routing_reason,
            },
            "input_hash": input_hash,
            "carry_state_hash": serde_json::Value::Null,
            "output_hash": output_hash,
            "result_len": result_len,
            "result_type": result.result_type(),
            "latency_ms": latency_ms,
        });

        if let Some(error) = result.error() {
            let map = record.as_object_mut().unwrap();
            map.insert("error_class".into(), serde_json::Value::String(error.error_class.clone()));
        }

        self.write_line(&record);
    }

    fn write_line(&mut self, value: &serde_json::Value) {
        // Best-effort: trace emission should not crash the runtime.
        let _ = serde_json::to_writer(&mut self.dest, value);
        let _ = self.dest.write_all(b"\n");
        let _ = self.dest.flush();
    }
}

/// Parameters for an invoke trace record — avoids a massive argument list.
pub struct InvokeTraceParams<'a> {
    pub trace_id: &'a str,
    pub session_id: &'a str,
    pub step: u64,
    pub graph_id: &'a str,
    pub graph_version: &'a str,
    pub brick_id: &'a str,
    pub brick_version: &'a str,
    pub bundle_digest: &'a str,
    pub node_id: &'a str,
    pub envelope_bytes: &'a [u8],
    pub trigger_source_node_id: &'a str,
    pub trigger_source_step: u64,
    pub trigger_edge_id: &'a str,
    pub trigger_routing_reason: &'a str,
}

fn sha256_prefixed(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    format!("sha256:{}", hex::encode(hasher.finalize()))
}
