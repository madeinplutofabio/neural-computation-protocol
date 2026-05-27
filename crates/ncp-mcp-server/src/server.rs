//! MCP stdio server wiring (PR C).
//!
//! Implements the manual `ServerHandler` for the NCP MCP adapter against
//! the runtime-loaded graphs assembled by `crate::cli`. Three RPC methods
//! are exposed:
//!
//! - `initialize` — default rmcp impl returns `self.get_info()` (per
//!   rmcp 1.7 `server_handler_methods!` macro). We only override
//!   `get_info` to advertise the `tools` capability with
//!   `listChanged: false` (per docs/MCP_ADAPTER.md §13: v0's tool list
//!   is static post-startup).
//! - `tools/list` — enumerates one `Tool` per loaded graph; input
//!   schema is the v0 `{"type":"object"}` object (per §4); descriptions
//!   embed `graph_id` + `graph_version`.
//! - `tools/call` — dispatches by tool name, runs the graph via
//!   `tokio::task::spawn_blocking` (which owns ALL blocking work
//!   including trace-writer construction), and builds a
//!   `CallToolResult` per §5 (`structuredContent` + text content
//!   mirror; rolled-up `result_type`; `isError` per §6 Class A).
//!
//! Error mapping (per docs/MCP_ADAPTER.md §6):
//!
//! - **Class A (graph outcomes):** Success / LowConfidence / Failure
//!   (including the adapter-synthesized empty-terminals Failure per
//!   §5) all return `Ok(CallToolResult)` with `isError` per §5
//!   truth table.
//! - **Class B (protocol / infra):** unknown tool name,
//!   trace-writer construction failure pre-execution,
//!   `spawn_blocking` JoinError, `execute()` returning `Err` →
//!   returned as `McpError` (JSON-RPC error response).
//! - **Class C (mid-execution trace I/O failure):** NOT detectable
//!   in v0 because `TraceSink::emit_*` returns `()`. See §6 Class C
//!   for the honest contract and the runtime API change that would
//!   unlock detection.
//!
//! Sync/async bridging (per §11): the rmcp tokio handler offloads
//! each `tools/call` to `spawn_blocking`, which is sound because
//! PR B's `send_sync_check` module verified `RuntimeContext: Send +
//! Sync` at compile time. ALL blocking work — trace writer file
//! creation, `emit_runtime_info`, and `execute()` itself — happens
//! inside the blocking closure. The async handler thread only
//! computes paths, clones `Arc`s, and forwards the JoinHandle.
//!
//! **rmcp model-struct construction discipline:** rmcp `#[non_exhaustive]`
//! model structs are built via `Default::default()` + field mutation because
//! struct literals are not available outside rmcp. Exhaustive rmcp model
//! structs use struct literals so clippy can enforce complete construction.
//! In this file, `CallToolResult`, `InitializeResult` (= `ServerInfo`), and
//! `ServerCapabilities` use Default + mutation; `ToolsCapability` is
//! exhaustive in rmcp 1.7 and is constructed with a literal.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use anyhow::{Context, Result};
use ncp_runtime::result::BrickResult;
use ncp_runtime::trace::{JsonlTraceWriter, NullTrace, TraceSink};
use ncp_runtime::{
    mapping, ExecuteHooks, ExecuteOptions, ExecutionReport, RuntimeContext,
    TerminalResult as RuntimeTerminalResult,
};
use rmcp::handler::server::ServerHandler;
use rmcp::model::{
    CallToolRequestParams, CallToolResult, Content, ErrorData as McpError, Implementation,
    JsonObject, ListToolsResult, PaginatedRequestParams, ServerCapabilities, ServerInfo, Tool,
    ToolsCapability,
};
use rmcp::service::{RequestContext, RoleServer};
use rmcp::transport::stdio;
use rmcp::ServiceExt;
use serde_json::{json, Map, Value};
use uuid::Uuid;

// ── Shared startup state (consumed by cli.rs) ───────────────────────

/// One MCP tool, backed by a loaded NCP graph.
///
/// Constructed by `crate::cli` during startup. The `context` is wrapped
/// in `Arc` so each `tools/call` can clone a cheap handle and move it
/// into a `tokio::task::spawn_blocking` closure without serializing
/// concurrent calls on a single shared owner.
pub struct ToolEntry {
    pub graph_path: PathBuf,
    pub graph_id: String,
    pub graph_version: String,
    pub context: Arc<RuntimeContext>,
}

/// Server startup state, assembled by `crate::cli::run` and consumed
/// by `run`.
///
/// `trace_dir` is the canonicalized directory path (per §12) or `None`
/// when `--trace-dir` was absent.
pub struct StartupState {
    pub tools: HashMap<String, ToolEntry>,
    pub trace_dir: Option<PathBuf>,
}

// ── Tokio entrypoint ────────────────────────────────────────────────

/// Build the rmcp server, attach it to stdio, and run until the peer
/// closes the connection.
///
/// `cli.rs` calls this inside `rt.block_on(...)` after the startup
/// summary has been written to stderr. Returning `Ok(())` here means
/// the MCP peer (or stdin EOF) cleanly ended the session.
pub async fn run(state: StartupState) -> Result<()> {
    let server = McpServer {
        tools: Arc::new(state.tools),
        trace_dir: state.trace_dir.map(Arc::new),
    };

    let running = server
        .serve(stdio())
        .await
        .context("failed to start MCP stdio service")?;

    let _quit_reason = running
        .waiting()
        .await
        .context("MCP service loop ended unexpectedly")?;
    Ok(())
}

// ── ServerHandler implementation ────────────────────────────────────

/// `ServerHandler` impl for the NCP MCP adapter.
///
/// All fields are `Arc`-wrapped because `ServerHandler` requires
/// `Send + Sync + 'static` and rmcp's `serve()` may internally clone /
/// share the handler across tasks. Read-only after construction.
#[derive(Clone)]
struct McpServer {
    tools: Arc<HashMap<String, ToolEntry>>,
    trace_dir: Option<Arc<PathBuf>>,
}

impl ServerHandler for McpServer {
    fn get_info(&self) -> ServerInfo {
        // Build ServerInfo via Default + field mutation because
        // `InitializeResult` (= `ServerInfo`) is `#[non_exhaustive]`
        // in rmcp 1.7. Struct-literal construction would fail to
        // compile from outside the rmcp crate.
        //
        // We explicitly do NOT call `Implementation::from_build_env()`
        // because that resolves `env!()` against the rmcp crate's
        // manifest (yielding `name: "rmcp"`). Instead we call
        // `Implementation::new(env!("CARGO_PKG_NAME"), env!("CARGO_PKG_VERSION"))`
        // so the macros expand against THIS crate, yielding the
        // correct `name: "ncp-mcp-server"`.
        let mut caps = ServerCapabilities::default();
        // Per §13: v0's tool list is static post-startup; we do NOT
        // emit notifications/tools/list_changed, so the capability
        // flag is explicitly `false`.
        //
        // `ToolsCapability` is exhaustive in rmcp 1.7, unlike
        // `ServerCapabilities`, `ServerInfo`, and `CallToolResult`. Use a
        // literal here so clippy can enforce complete construction; use
        // Default + mutation only for non-exhaustive types.
        caps.tools = Some(ToolsCapability {
            list_changed: Some(false),
        });

        let mut info = ServerInfo::default();
        info.capabilities = caps;
        info.server_info = Implementation::new(env!("CARGO_PKG_NAME"), env!("CARGO_PKG_VERSION"));
        info.instructions = None;
        info
    }

    async fn list_tools(
        &self,
        _request: Option<PaginatedRequestParams>,
        _context: RequestContext<RoleServer>,
    ) -> Result<ListToolsResult, McpError> {
        // Sort tool names deterministically — HashMap iteration order
        // is otherwise non-deterministic and clients tend to display
        // tools in the order returned.
        let mut names: Vec<&String> = self.tools.keys().collect();
        names.sort();

        let tools: Vec<Tool> = names
            .into_iter()
            .map(|name| {
                let entry = &self.tools[name];
                Tool::new(
                    name.clone(),
                    format!(
                        "NCP graph {} ({}). Object-shaped arguments are passed verbatim as the graph root input.",
                        entry.graph_id, entry.graph_version
                    ),
                    Arc::new(object_input_schema()),
                )
            })
            .collect();

        Ok(ListToolsResult::with_all_items(tools))
    }

    async fn call_tool(
        &self,
        request: CallToolRequestParams,
        _context: RequestContext<RoleServer>,
    ) -> Result<CallToolResult, McpError> {
        // ── Class B: unknown tool name ─────────────────────────────
        let tool_name = request.name.as_ref();
        let entry = self
            .tools
            .get(tool_name)
            .ok_or_else(|| McpError::invalid_params(format!("unknown tool: {tool_name}"), None))?;

        // Pre-allocate trace_id so the SAME id appears in both the
        // trace file path and the structured response (per §12).
        let trace_id = Uuid::new_v4().to_string();

        // Compute trace path ON the async handler thread (cheap —
        // PathBuf::join, no I/O). The actual file create happens
        // INSIDE spawn_blocking so the blocking syscall stays off
        // the tokio runtime threads.
        let trace_path: Option<PathBuf> = self
            .trace_dir
            .as_ref()
            .map(|dir| dir.join(format!("{trace_id}.jsonl")));

        // MCP `arguments` is `Option<JsonObject>`. v0 (per §4) accepts
        // any object and passes it verbatim as the graph root input.
        // Absent arguments become `{}` for consistency.
        let arguments_value: Value = request
            .arguments
            .map(Value::Object)
            .unwrap_or_else(|| Value::Object(Map::new()));

        let ctx = Arc::clone(&entry.context);
        let trace_id_for_blocking = trace_id.clone();
        let trace_path_for_blocking = trace_path.clone();

        // ALL blocking work runs here: trace-writer construction
        // (filesystem create), `emit_runtime_info` (writes one JSONL
        // record), and `execute()` itself. The closure returns
        // `anyhow::Result<ExecutionReport>` so writer-construction
        // failure and `execute()` failure flow through the same
        // Class B error path below.
        let execution_result = tokio::task::spawn_blocking(move || -> Result<ExecutionReport> {
            let mut tracer: Box<dyn TraceSink + Send> = match &trace_path_for_blocking {
                Some(path) => Box::new(JsonlTraceWriter::file(path).with_context(|| {
                    format!("trace writer construction failed for `{}`", path.display())
                })?),
                None => Box::new(NullTrace),
            };

            ctx.emit_runtime_info(tracer.as_mut());

            let mut hooks = ExecuteHooks::default();
            let opts = ExecuteOptions {
                trace_id: Some(trace_id_for_blocking),
                ..Default::default()
            };

            ctx.execute(&arguments_value, tracer.as_mut(), &mut hooks, &opts)
        })
        .await;

        // ── Class B: spawn_blocking join failure (panic / cancellation) ──
        let report = execution_result
            .map_err(|join_err| {
                McpError::internal_error(
                    format!("graph execution task panicked or was cancelled: {join_err}"),
                    None,
                )
            })?
            // ── Class B: closure returned Err — either trace-writer
            // construction failed OR `execute()` returned a
            // runtime-level error (missing brick key, queue overflow,
            // broken edge mapping). Both surface as JSON-RPC errors.
            // "tool call execution failed" because this path covers
            // both pre-execution (writer construction) and execution
            // failures; "graph execution failed" would be misleading
            // when the graph never ran.
            .map_err(|exec_err| {
                McpError::internal_error(format!("tool call execution failed: {exec_err:#}"), None)
            })?;

        // ── Class A: build the §5 response shape from the report ──
        Ok(build_call_tool_result(
            &report,
            &trace_id,
            trace_path.as_deref(),
        ))
    }
}

// ── Response shape builders (per §5) ────────────────────────────────

/// Build the v0 tool input schema literal: `{ "type": "object" }`.
///
/// Per §4: v0 declares an object-shaped input; the MCP `arguments`
/// object is passed verbatim as the graph root input. No per-graph
/// schema declaration in v0.
fn object_input_schema() -> JsonObject {
    let mut m = Map::new();
    m.insert("type".to_string(), Value::String("object".to_string()));
    m
}

/// Build a `CallToolResult` from an `ExecutionReport` per §5.
///
/// Three branches:
///
/// 1. **Empty terminals (adapter-synthesized Failure per §5):**
///    `execute()` returned `Ok` but with no terminal results. Adapter
///    refuses to report Success because no graph output was produced.
///    Synthesizes a top-level `NO_TERMINAL_RESULTS` adapter error.
/// 2. **Non-empty, all Success:** rolled-up `result_type: "Success"`,
///    `isError: false`.
/// 3. **Non-empty, any Failure / LowConfidence:** rolled-up
///    `result_type` per §5 table, `isError: true`.
///
/// The text-content array mirrors `structuredContent` as a single
/// JSON-stringified entry (per §5 "text content mirror").
///
/// Constructed via `Default` + field mutation because `CallToolResult`
/// is `#[non_exhaustive]` in rmcp 1.7.
fn build_call_tool_result(
    report: &ExecutionReport,
    trace_id: &str,
    trace_path: Option<&Path>,
) -> CallToolResult {
    let terminal_results_json: Vec<Value> = report
        .terminals
        .iter()
        .map(terminal_result_to_json)
        .collect();

    let trace_path_value = trace_path
        .map(|p| Value::String(p.display().to_string()))
        .unwrap_or(Value::Null);

    let (structured, is_error) = if report.terminals.is_empty() {
        // Adapter-synthesized Failure: see §5 "Empty terminal results".
        let structured = json!({
            "result_type": "Failure",
            "output_json": Value::Null,
            "trace_id": trace_id,
            "trace_path": trace_path_value,
            "terminal_results": terminal_results_json,
            "error": {
                "class": "NO_TERMINAL_RESULTS",
                "message": "Graph execution completed without terminal results",
            },
        });
        (structured, true)
    } else {
        let (rolled_up_type, is_error) = rollup_status(report);
        let output_json = derive_output_json(&report.terminals, &terminal_results_json);
        let structured = json!({
            "result_type": rolled_up_type,
            "output_json": output_json,
            "trace_id": trace_id,
            "trace_path": trace_path_value,
            "terminal_results": terminal_results_json,
        });
        (structured, is_error)
    };

    let text_mirror = structured.to_string();

    let mut result = CallToolResult::default();
    result.content = vec![Content::text(text_mirror)];
    result.structured_content = Some(structured);
    result.is_error = Some(is_error);
    result
}

/// Roll terminal-level result types up to a single top-level value
/// (per §5 "top-level result_type" table). The boolean is `isError`.
///
/// **Precondition:** `report.terminals` is non-empty. The empty case
/// is handled by `build_call_tool_result` before this function is
/// called (it represents an adapter-synthesized Failure with
/// dedicated shape, not a rollup of zero terminals).
fn rollup_status(report: &ExecutionReport) -> (&'static str, bool) {
    let mut any_failure = false;
    let mut any_low_conf = false;
    for t in &report.terminals {
        match &t.result {
            BrickResult::Failure { .. } => any_failure = true,
            BrickResult::LowConfidence { .. } => any_low_conf = true,
            BrickResult::Success { .. } => {}
        }
    }
    if any_failure {
        ("Failure", true)
    } else if any_low_conf {
        ("LowConfidence", true)
    } else {
        ("Success", false)
    }
}

/// Convert one runtime `TerminalResult` to its §5 JSON representation:
/// `node_id`, `brick_id`, `step`, `result_type`, optional `output`
/// (Success/LowConfidence only), optional `error`
/// (LowConfidence/Failure only).
///
/// `err.error_class` and `err.message` are accessed by reference to
/// avoid moving out of `&ErrorObject` (those fields are `String`,
/// non-`Copy`). `json!` accepts `&T: Serialize` for object values.
/// `step` uses `json!(t.step)` to stay type-agnostic if the runtime
/// ever changes the field's integer type.
fn terminal_result_to_json(t: &RuntimeTerminalResult) -> Value {
    let mut obj = Map::new();
    obj.insert("node_id".to_string(), Value::String(t.node_id.clone()));
    obj.insert("brick_id".to_string(), Value::String(t.brick_id.clone()));
    obj.insert("step".to_string(), json!(t.step));
    obj.insert(
        "result_type".to_string(),
        Value::String(t.result.result_type().to_string()),
    );
    if let Some(output) = t.result.output() {
        obj.insert("output".to_string(), mapping::cbor_to_json(output));
    }
    if let Some(err) = t.result.error() {
        obj.insert(
            "error".to_string(),
            json!({
                "class": &err.error_class,
                "message": &err.message,
            }),
        );
    }
    Value::Object(obj)
}

/// Derive the `output_json` convenience field per §5 truth table:
///
/// | Terminal results | `output_json` |
/// |---|---|
/// | exactly one Success or LowConfidence terminal | that terminal's output JSON |
/// | multiple terminal results (any mix) | `{ "terminal_results": [...] }` |
/// | Failure-only (no usable output across terminals) | `null` |
///
/// **Precondition:** `terminals` is non-empty. The empty case is
/// handled by the dedicated adapter-synthesized Failure branch in
/// `build_call_tool_result`.
fn derive_output_json(terminals: &[RuntimeTerminalResult], all_json: &[Value]) -> Value {
    if terminals.len() == 1 {
        match terminals[0].result.output() {
            Some(output) => mapping::cbor_to_json(output),
            None => Value::Null,
        }
    } else if terminals.iter().all(|t| t.result.output().is_none()) {
        Value::Null
    } else {
        json!({ "terminal_results": all_json })
    }
}
