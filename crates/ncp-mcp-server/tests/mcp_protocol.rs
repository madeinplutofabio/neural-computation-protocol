//! End-to-end MCP protocol tests for `ncp-mcp-server` (PR C scope).
//!
//! Every test in this file drives the built binary as a subprocess via
//! Cargo's `CARGO_BIN_EXE_ncp-mcp-server` env var (avoids hardcoded
//! `target/debug/...` paths that break on Windows `.exe` suffix or
//! release profile). The subprocess speaks JSON-RPC 2.0 over stdio —
//! these tests exercise the wire shape directly, without depending on
//! rmcp's client-side abstractions.
//!
//! This is the "real protocol guard" per docs/MCP_ADAPTER.md §7 and
//! per the PR C plan. It catches:
//!
//! - **Stdout pollution:** every stdout line read via `read_frame()`
//!   must parse as JSON AND have `jsonrpc == "2.0"`. Anything that
//!   leaks to stdout from logging, panics, or third-party
//!   instrumentation fails the assertion.
//! - **Wire-shape regressions:** `tools/list` + `tools/call` responses
//!   are validated against the §5 structured shape.
//! - **Class A vs Class B error mapping:** unknown tool name MUST
//!   return JSON-RPC error (Class B), NOT a successful response with
//!   `isError: true`. Malformed JSON-RPC MUST return an error
//!   response. Per §6 these distinctions are the most easily
//!   conflated in MCP integrations.
//! - **Concurrent dispatch correctness:** 4 in-flight `tools/call`s
//!   all complete with distinct ids + distinct trace_ids, and all 4
//!   trace files end up on disk. Behavioral concurrency assertion —
//!   no wall-clock timing, robust against CI noise.
//!
//! All subprocess interactions are wrapped in `tokio::time::timeout`
//! with a 10s deadline. On test failure (assertion or timeout),
//! `Server`'s `Drop` impl calls `start_kill()` so no orphan processes
//! survive in CI. The runtime is `#[tokio::test(flavor = "multi_thread")]`
//! so that subprocess I/O and timeout polling can run in parallel
//! reliably even when a test happens to run alone.
//!
//! **stderr discipline:** the child's stderr is routed to `Stdio::null()`
//! at OS level. We intentionally do NOT pipe stderr because we never
//! drain it — a noisy failure could fill the pipe buffer (64KB on
//! Linux) and deadlock the subprocess. Discarding via the null device
//! preserves the "stderr content is irrelevant" stance from the plan
//! while removing the deadlock hazard.

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::Duration;

use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, ChildStdout, Command};
use tokio::time::timeout;

const RPC_TIMEOUT: Duration = Duration::from_secs(10);

// ── Workspace path helpers (mirror tests/loading.rs) ────────────────

fn binary() -> &'static str {
    env!("CARGO_BIN_EXE_ncp-mcp-server")
}

fn workspace_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|p| p.parent())
        .expect("CARGO_MANIFEST_DIR has at least two parents")
        .to_path_buf()
}

fn echo_pipeline_graph() -> PathBuf {
    workspace_root().join("examples/graphs/echo-pipeline/graph.yaml")
}

fn brick_dir() -> PathBuf {
    workspace_root().join("examples/bricks")
}

/// Unique temp dir per test invocation, including a process-global
/// atomic counter so parallel `cargo test` invocations within the same
/// binary don't collide.
fn unique_temp_dir(test_name: &str) -> PathBuf {
    use std::sync::atomic::{AtomicUsize, Ordering};
    static COUNTER: AtomicUsize = AtomicUsize::new(0);
    let n = COUNTER.fetch_add(1, Ordering::Relaxed);
    let p = std::env::temp_dir().join(format!(
        "ncp-mcp-server-mcp-test-{}-{}-{}",
        test_name,
        std::process::id(),
        n,
    ));
    let _ = std::fs::remove_dir_all(&p);
    p
}

// ── Subprocess driver ───────────────────────────────────────────────

/// A running `ncp-mcp-server` subprocess with piped stdin/stdout and
/// `Stdio::null()` on stderr. Dropping the `Server` kills the child
/// (non-blocking `start_kill`) so test panics or assertion failures
/// never leave orphan processes.
struct Server {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
    next_id: i64,
}

impl Server {
    async fn spawn(trace_dir: Option<&Path>) -> Self {
        let mut cmd = Command::new(binary());
        cmd.arg("--graph")
            .arg(echo_pipeline_graph())
            .arg("--brick-dir")
            .arg(brick_dir());
        if let Some(td) = trace_dir {
            cmd.arg("--trace-dir").arg(td);
        }
        // stderr goes to /dev/null (or NUL on Windows). Piping without
        // draining could deadlock the child if its stderr buffer
        // fills (64KB on Linux). The plan allows stderr to contain
        // arbitrary output; discarding satisfies that.
        cmd.stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());

        let mut child = cmd
            .spawn()
            .expect("failed to spawn ncp-mcp-server subprocess");
        let stdin = child.stdin.take().expect("piped stdin");
        let stdout = child.stdout.take().expect("piped stdout");
        Server {
            child,
            stdin,
            stdout: BufReader::new(stdout),
            next_id: 1,
        }
    }

    fn next_id(&mut self) -> i64 {
        let id = self.next_id;
        self.next_id += 1;
        id
    }

    /// Write one JSON-RPC frame (one line + LF) to stdin. JSON-RPC
    /// over stdio uses LF-delimited framing regardless of host OS.
    async fn send_frame(&mut self, frame: &Value) {
        let line = format!("{}\n", frame);
        timeout(RPC_TIMEOUT, self.stdin.write_all(line.as_bytes()))
            .await
            .expect("timed out writing to subprocess stdin")
            .expect("write to subprocess stdin failed");
        timeout(RPC_TIMEOUT, self.stdin.flush())
            .await
            .expect("timed out flushing subprocess stdin")
            .expect("flush of subprocess stdin failed");
    }

    /// Write raw bytes verbatim to stdin (for malformed-input tests
    /// that intentionally do not produce a serialized `Value`).
    async fn send_raw(&mut self, bytes: &[u8]) {
        timeout(RPC_TIMEOUT, self.stdin.write_all(bytes))
            .await
            .expect("timed out writing raw bytes to subprocess stdin")
            .expect("raw write to subprocess stdin failed");
        timeout(RPC_TIMEOUT, self.stdin.flush())
            .await
            .expect("timed out flushing subprocess stdin")
            .expect("flush of subprocess stdin failed");
    }

    /// Read one line from stdout and parse it as JSON. Asserts the
    /// line is well-formed JSON-RPC 2.0. This is the §7 stdout-
    /// discipline guard.
    async fn read_frame(&mut self) -> Value {
        let mut line = String::new();
        let bytes_read = timeout(RPC_TIMEOUT, self.stdout.read_line(&mut line))
            .await
            .expect("timed out reading subprocess stdout")
            .expect("read from subprocess stdout failed");
        assert!(
            bytes_read > 0,
            "subprocess closed stdout before sending a frame"
        );
        let parsed: Value = serde_json::from_str(line.trim_end())
            .unwrap_or_else(|e| panic!("stdout line is not valid JSON: {e}\n  line: {line:?}"));
        assert_eq!(
            parsed.get("jsonrpc"),
            Some(&json!("2.0")),
            "stdout line is not JSON-RPC 2.0: {parsed}",
        );
        parsed
    }

    /// Perform the `initialize` request + `notifications/initialized`
    /// notification handshake. Returns the initialize response.
    async fn initialize(&mut self) -> Value {
        let id = self.next_id();
        let init = json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "ncp-mcp-server-test", "version": "0.1.0"},
            },
        });
        self.send_frame(&init).await;
        let response = self.read_frame().await;
        assert_eq!(
            response.get("id"),
            Some(&json!(id)),
            "initialize response id mismatch: {response}"
        );

        let initialized = json!({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        });
        self.send_frame(&initialized).await;
        response
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        // start_kill is non-blocking and safe in Drop. This is a fallback
        // cleanup path for panics/timeouts; tests do not rely on graceful
        // shutdown here.
        let _ = self.child.start_kill();
    }
}

// ── Tests ──────────────────────────────────────────────────────────

/// `initialize` succeeds with the `tools` capability advertised and
/// `tools/list` enumerates exactly one tool (the echo-pipeline graph).
#[tokio::test(flavor = "multi_thread")]
async fn initialize_and_tools_list() {
    let mut server = Server::spawn(None).await;
    let init_response = server.initialize().await;

    let result = init_response.get("result").expect("initialize result");
    assert!(
        result["capabilities"]["tools"].is_object(),
        "expected tools capability in initialize response: {result}"
    );
    // §13 contract: listChanged is explicitly false (not omitted).
    assert_eq!(
        result["capabilities"]["tools"]["listChanged"],
        json!(false),
        "listChanged must be false per §13: {result}"
    );

    // tools/list
    let id = server.next_id();
    let req = json!({
        "jsonrpc": "2.0",
        "id": id,
        "method": "tools/list",
        "params": {},
    });
    server.send_frame(&req).await;
    let resp = server.read_frame().await;
    assert_eq!(resp.get("id"), Some(&json!(id)));
    assert!(resp.get("error").is_none(), "tools/list errored: {resp}");

    let tools = resp["result"]["tools"]
        .as_array()
        .expect("tools/list result must contain tools array");
    assert_eq!(tools.len(), 1, "expected exactly one loaded tool");
    assert_eq!(
        tools[0]["name"], "org.ncp-examples.echo-pipeline",
        "expected derived tool name to match graph_id"
    );
    // §4: input schema is `{"type":"object"}`.
    assert_eq!(tools[0]["inputSchema"]["type"], json!("object"));
}

/// `tools/call` against the echo graph returns a Class A successful
/// response with `isError: false` and the §5 structuredContent shape.
#[tokio::test(flavor = "multi_thread")]
async fn tools_call_returns_class_a_success() {
    let mut server = Server::spawn(None).await;
    server.initialize().await;

    let id = server.next_id();
    let req = json!({
        "jsonrpc": "2.0",
        "id": id,
        "method": "tools/call",
        "params": {
            "name": "org.ncp-examples.echo-pipeline",
            "arguments": {"hello": "world"},
        },
    });
    server.send_frame(&req).await;
    let resp = server.read_frame().await;

    assert_eq!(resp.get("id"), Some(&json!(id)));
    assert!(
        resp.get("error").is_none(),
        "Class A: tools/call must NOT be a JSON-RPC error: {resp}"
    );

    let result = resp.get("result").expect("tools/call result");
    assert_eq!(
        result["isError"],
        json!(false),
        "Success rollup must set isError: false"
    );

    let sc = result.get("structuredContent").expect("structuredContent");
    assert_eq!(sc["result_type"], json!("Success"));
    assert!(sc["trace_id"].is_string(), "trace_id must be present");
    assert_eq!(
        sc["trace_path"],
        Value::Null,
        "trace_path must be null when --trace-dir is not set"
    );
    assert!(
        sc["terminal_results"].is_array(),
        "terminal_results must be present"
    );
    assert!(
        !sc["terminal_results"].as_array().unwrap().is_empty(),
        "echo graph must produce at least one terminal result"
    );

    // §5 text-content mirror: content[0] is a text item whose body is
    // the JSON serialization of structuredContent.
    let content = result["content"].as_array().expect("content array");
    assert_eq!(content.len(), 1);
    assert_eq!(content[0]["type"], json!("text"));
    let mirror: Value = serde_json::from_str(content[0]["text"].as_str().expect("text string"))
        .expect("content[0].text must be valid JSON");
    assert_eq!(
        &mirror, sc,
        "text content must be the JSON serialization of structuredContent"
    );
}

/// `tools/call` with an unknown tool name returns a Class B JSON-RPC
/// error response — NOT a successful result with `isError: true`. Per
/// §6, conflating these is the most common MCP integration bug.
#[tokio::test(flavor = "multi_thread")]
async fn unknown_tool_returns_jsonrpc_error() {
    let mut server = Server::spawn(None).await;
    server.initialize().await;

    let id = server.next_id();
    let req = json!({
        "jsonrpc": "2.0",
        "id": id,
        "method": "tools/call",
        "params": {
            "name": "this.tool.does.not.exist",
            "arguments": {},
        },
    });
    server.send_frame(&req).await;
    let resp = server.read_frame().await;

    assert_eq!(resp.get("id"), Some(&json!(id)));
    assert!(
        resp.get("error").is_some(),
        "Class B: unknown tool must return JSON-RPC error, got: {resp}"
    );
    assert!(
        resp.get("result").is_none(),
        "Class B: JSON-RPC error response must NOT have 'result', got: {resp}"
    );
}

/// Malformed JSON-RPC input returns a JSON-RPC error response. We do
/// NOT overfit the exact error message — the assertion is purely on
/// the response shape: `error` present, `result` absent.
#[tokio::test(flavor = "multi_thread")]
async fn malformed_input_returns_jsonrpc_error() {
    let mut server = Server::spawn(None).await;
    server.initialize().await;

    // Syntactically invalid JSON-RPC frame. LF-terminated so the
    // server's line reader actually attempts to parse it.
    server.send_raw(b"{ this is definitely not json }\n").await;

    let resp = server.read_frame().await;
    assert!(
        resp.get("error").is_some(),
        "malformed input must return JSON-RPC error, got: {resp}"
    );
    assert!(
        resp.get("result").is_none(),
        "JSON-RPC error response must NOT have 'result', got: {resp}"
    );
}

/// 4 in-flight `tools/call` requests against the echo graph all
/// complete with distinct ids, distinct trace_ids, all `isError:
/// false`, and all 4 trace files end up on disk. Behavioral
/// concurrency assertion — no wall-clock timing.
#[tokio::test(flavor = "multi_thread")]
async fn concurrent_calls_complete_independently() {
    const N: usize = 4;

    let trace_dir = unique_temp_dir("concurrent");
    let mut server = Server::spawn(Some(&trace_dir)).await;
    server.initialize().await;

    // Send all N requests without reading any responses in between.
    let mut sent_ids: Vec<i64> = Vec::with_capacity(N);
    for i in 0..N {
        let id = server.next_id();
        sent_ids.push(id);
        let req = json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": "tools/call",
            "params": {
                "name": "org.ncp-examples.echo-pipeline",
                "arguments": {"call_number": i},
            },
        });
        server.send_frame(&req).await;
    }

    // Read N responses. Order is unconstrained — correlate by id.
    let mut responses: Vec<Value> = Vec::with_capacity(N);
    for _ in 0..N {
        responses.push(server.read_frame().await);
    }

    // All N sent ids appear in the N responses, each exactly once.
    let response_ids: HashSet<i64> = responses
        .iter()
        .filter_map(|r| r.get("id").and_then(Value::as_i64))
        .collect();
    let sent_id_set: HashSet<i64> = sent_ids.iter().copied().collect();
    assert_eq!(
        response_ids.len(),
        N,
        "expected {N} distinct response ids, got: {response_ids:?}"
    );
    assert_eq!(
        response_ids, sent_id_set,
        "response ids do not match sent ids"
    );

    // All N responses are Class A successes with isError: false.
    for r in &responses {
        assert!(
            r.get("error").is_none(),
            "concurrent call returned JSON-RPC error: {r}"
        );
        assert_eq!(
            r["result"]["isError"],
            json!(false),
            "concurrent call had isError: true: {r}"
        );
    }

    // All N trace_ids are distinct.
    let trace_ids: Vec<String> = responses
        .iter()
        .map(|r| {
            r["result"]["structuredContent"]["trace_id"]
                .as_str()
                .expect("structuredContent.trace_id must be a string")
                .to_string()
        })
        .collect();
    let trace_id_set: HashSet<&String> = trace_ids.iter().collect();
    assert_eq!(
        trace_id_set.len(),
        N,
        "expected {N} distinct trace_ids, got: {trace_ids:?}"
    );

    // All N trace files exist on disk and are non-empty.
    for tid in &trace_ids {
        let path = trace_dir.join(format!("{tid}.jsonl"));
        assert!(
            path.exists(),
            "trace file missing for trace_id {tid}: {}",
            path.display()
        );
        let meta = std::fs::metadata(&path).expect("metadata on trace file");
        assert!(
            meta.len() > 0,
            "trace file is empty for trace_id {tid}: {}",
            path.display()
        );
    }

    // Each response's structuredContent.trace_path must MATCH the
    // expected `<trace-dir>/<trace_id>.jsonl` for that response's
    // trace_id. Canonicalize both sides before comparing because the
    // server-side trace-dir is canonicalized at startup (per §12); on
    // Windows the response path carries a `\\?\` UNC prefix and on
    // macOS `/tmp` resolves to `/private/tmp`. Raw `PathBuf`
    // comparison would give false negatives on those platforms.
    for r in &responses {
        let sc = &r["result"]["structuredContent"];

        let trace_id = sc["trace_id"].as_str().expect("trace_id must be a string");

        let trace_path_str = sc["trace_path"]
            .as_str()
            .expect("trace_path must be a string when --trace-dir is set");

        let actual = PathBuf::from(trace_path_str);
        assert!(
            actual.exists(),
            "structuredContent.trace_path does not exist on disk: {}",
            actual.display()
        );

        let expected = trace_dir.join(format!("{trace_id}.jsonl"));
        assert!(
            expected.exists(),
            "expected trace file does not exist on disk: {}",
            expected.display()
        );

        let actual_canonical = std::fs::canonicalize(&actual)
            .expect("actual trace_path must canonicalize after existence check");
        let expected_canonical = std::fs::canonicalize(&expected)
            .expect("expected trace file must canonicalize after existence check");

        assert_eq!(
            actual_canonical, expected_canonical,
            "structuredContent.trace_path must match <trace-dir>/<trace_id>.jsonl"
        );
    }

    drop(server); // explicit; otherwise Drop kills the child anyway.
    let _ = std::fs::remove_dir_all(&trace_dir);
}
