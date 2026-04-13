use std::io::Write;

use sha2::{Digest, Sha256};

use crate::result::BrickResult;

/// Trace schema version — included in runtime_info and every invoke record.
const TRACE_SCHEMA_VERSION: &str = "ncp-trace-0.1";

// ── TraceSink trait ────────────────────────────────────────────────

/// Pluggable trace emission. JsonlTraceWriter for CLI; NullTrace for bench.
pub trait TraceSink: Send {
    /// Returns false to skip all trace work (timestamp allocation, serialization).
    /// NullTrace returns false; real writers return true (default).
    fn enabled(&self) -> bool { true }

    fn emit_runtime_info(&mut self, runtime_version: &str, wasmtime_version: &str, timestamp: &str);
    fn emit_invoke(
        &mut self,
        trace_id: &str,
        session_id: &str,
        step: u64,
        graph_id: &str,
        graph_version: &str,
        brick_id: &str,
        brick_version: &str,
        bundle_digest: &str,
        node_id: &str,
        envelope_bytes: &[u8],
        trigger_source_node_id: &str,
        trigger_source_step: u64,
        trigger_edge_id: &str,
        trigger_routing_reason: &str,
        result: &BrickResult,
        result_bytes: Option<&[u8]>,
        latency_ms: f64,
        timestamp: &str,
    );
}

/// No-op trace sink for benchmarks — zero overhead.
pub struct NullTrace;

impl TraceSink for NullTrace {
    fn enabled(&self) -> bool { false }
    fn emit_runtime_info(&mut self, _: &str, _: &str, _: &str) {}
    fn emit_invoke(
        &mut self, _: &str, _: &str, _: u64, _: &str, _: &str, _: &str, _: &str,
        _: &str, _: &str, _: &[u8], _: &str, _: u64, _: &str, _: &str,
        _: &BrickResult, _: Option<&[u8]>, _: f64, _: &str,
    ) {}
}

// ── JsonlTraceWriter ───────────────────────────────────────────────

/// JSON Lines trace writer — emits to stderr or a file.
pub struct JsonlTraceWriter {
    dest: Box<dyn Write + Send>,
}

impl JsonlTraceWriter {
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

    fn write_line(&mut self, value: &serde_json::Value) {
        let _ = serde_json::to_writer(&mut self.dest, value);
        let _ = self.dest.write_all(b"\n");
    }
}

impl TraceSink for JsonlTraceWriter {
    fn emit_runtime_info(
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

    fn emit_invoke(
        &mut self,
        trace_id: &str,
        session_id: &str,
        step: u64,
        graph_id: &str,
        graph_version: &str,
        brick_id: &str,
        brick_version: &str,
        bundle_digest: &str,
        node_id: &str,
        envelope_bytes: &[u8],
        trigger_source_node_id: &str,
        trigger_source_step: u64,
        trigger_edge_id: &str,
        trigger_routing_reason: &str,
        result: &BrickResult,
        result_bytes: Option<&[u8]>,
        latency_ms: f64,
        timestamp: &str,
    ) {
        let input_hash = sha256_prefixed(envelope_bytes);
        let output_hash = result_bytes.map(|b| sha256_prefixed(b));
        let result_len = result_bytes.map(|b| b.len());

        let mut record = serde_json::json!({
            "type": "invoke",
            "trace_schema_version": TRACE_SCHEMA_VERSION,
            "timestamp": timestamp,
            "trace_id": trace_id,
            "session_id": session_id,
            "step": step,
            "graph_id": graph_id,
            "graph_version": graph_version,
            "brick_id": brick_id,
            "brick_version": brick_version,
            "bundle_digest": bundle_digest,
            "node_id": node_id,
            "trigger": {
                "source_node_id": trigger_source_node_id,
                "source_step": trigger_source_step,
                "edge_id": trigger_edge_id,
                "routing_reason": trigger_routing_reason,
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
}

fn sha256_prefixed(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    format!("sha256:{}", hex::encode(hasher.finalize()))
}
