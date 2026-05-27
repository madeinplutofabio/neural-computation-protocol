<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# NCP MCP Adapter — Design

`ncp-mcp-server` is a thin adapter that exposes NCP graphs as
[Model Context Protocol](https://modelcontextprotocol.io) tools over stdio
JSON-RPC. After installing it, an adopter configures their MCP-compatible
host to launch `ncp-mcp-server --graph graph.yaml` and the graph becomes a
callable tool with no glue code.

This document freezes the v0 design before any code lands. Architectural
decisions baked in here drive PRs B–E of Phase 3A.2.

**Targeted MCP spec version:** 2025-11-25 (the current stable).
The SDK choice (`rmcp v1.7.x`) implements this spec version.

---

## 1. Scope

**v0 transport: stdio ONLY.**

Stdio is the MCP transport for local hosts that launch the server as a
subprocess and communicate over stdin/stdout JSON-RPC. It's the
lowest-friction adopter path: no network listeners, no auth, no TLS, no
port management. Adopters configure a host (e.g. an MCP-compatible
desktop client or CLI host) to spawn the adapter as a child process,
and the host handles all the stdin/stdout plumbing.

**Streamable HTTP is deferred** to a future phase. (Note: the current
MCP spec replaced the legacy HTTP+SSE transport with "Streamable HTTP"
— any older docs referencing "SSE" as the web transport are stale.) An
HTTP-shaped transport adds non-trivial security surface (origin
validation against DNS rebinding, localhost binding for local servers,
auth for remote connections) that's worth getting right separately, on
demand, with its own design pass.

---

## 2. Mapping: one graph = one MCP tool

Each `--graph <path>` flag loaded at startup becomes exactly one MCP
tool. Multi-graph is supported from day one via repeated `--graph`:

```bash
ncp-mcp-server --graph graphs/triage.yaml --graph graphs/extract.yaml
```

The server's `tools/list` response enumerates one entry per loaded
graph. The server's `tools/call` dispatches the requested tool name to
the matching graph's `RuntimeContext::execute()`.

**Unique-name validation at startup.** After applying the
tool-name derivation rule (§3), the server checks that no two loaded
graphs produced the same tool name. Collision = startup failure with
a clear error message naming both colliding graph manifests. No
runtime hash-shortening or silent disambiguation — fail-fast is
transparent.

### CLI surface

The adapter binary accepts these flags:

| Flag | Required | Default | Purpose |
|---|---|---|---|
| `--graph <PATH>` | yes (repeat for multi-graph) | — | Path to an NCP graph manifest. Each occurrence loads one graph; together they map to one MCP tool each. |
| `--brick-dir <DIR>` | no | `examples/bricks` | Directory containing brick subdirectories. Passed through to `RuntimeContext::load()`. |
| `--brick-map <FILE>` | no | — | Brick-map file; overrides `--brick-dir` for listed brick IDs. Passed through to `RuntimeContext::load()`. |
| `--trace-dir <DIR>` | no | — | Directory for per-call trace files. See §12 for full semantics. If absent, traces are dropped (`NullTrace`). |

`--brick-dir` and `--brick-map` mirror the existing `ncp` runtime CLI semantics — adopters who already use the runtime CLI know these flags.

The MCP protocol layer (`initialize` / `tools/list` / `tools/call`) takes no CLI flags; transport is implicit (stdio).

---

## 3. Tool-name derivation rule

**Locked rule:**

```
tool_name = graph_id with every char NOT in [A-Za-z0-9_.-] replaced by '_'
```

**Worked example:** the example graph `examples/graphs/echo-pipeline/graph.yaml`
has `graph_id: org.ncp-examples.echo-pipeline`. All characters are
already in `[A-Za-z0-9_.-]`, so the derived tool name is
`org.ncp-examples.echo-pipeline` — unchanged.

**Why dots are preserved:** the MCP spec explicitly allows tool names
to contain dots (the spec's own example is `admin.tools.list`). Stripping
dots would lose reverse-domain namespace semantics and reduce
recognizability. The earlier instinct to "avoid dots in case clients
mishandle them" was client-rumor, not spec-aligned.

**Length / shape rules from the MCP spec:**
- Tool names are 1–128 characters.
- Case-sensitive.
- Must be unique within a server.
- Allowed chars: `[A-Za-z0-9_.-]`.

**Startup-failure conditions (no silent normalization):**

| Condition | Behavior |
|---|---|
| Derived name is empty (graph_id was empty) | startup fail |
| Derived name exceeds 128 chars | startup fail |
| Two loaded graphs derive the same name | startup fail (name both colliding manifests) |

**Future opt-in (deferred):** a `--tool-name-style underscore` flag
that converts dots to underscores, for any MCP host found to mishandle
dots. Not implemented in v0 because the spec is the authoritative
contract, not client behavior.

**No override flag in v0.** A per-graph `--tool-name <override>` flag
is a reasonable follow-up once base ergonomics are tested. Out of
scope for the first release.

---

## 4. Tool input schema (v0)

**v0 input schema for every tool:** `{ "type": "object" }`.

The MCP `arguments` field in `tools/call` is always a JSON object
(per spec). The adapter passes this object verbatim as the NCP graph
root input — no wrapping, no field extraction, no schema validation
on the adapter side. Any brick-level validation happens downstream.
The adapter does not pretend to validate a schema it does not own.

### MCP arguments conversion (locked)

MCP `tools/call` arguments are object-shaped. The adapter converts
them to the NCP root input as follows:

| MCP `arguments` | NCP graph input |
|---|---|
| omitted / null | `{}` (empty JSON object) |
| object | same object, passed verbatim |
| non-object (string, number, array, boolean) | JSON-RPC invalid-params error |

The adapter never wraps the object under another key. The graph
receives exactly the object the MCP client sent. The omitted/null
case maps to an empty object (not to null itself) so graphs that
expect an object can always assume one is present.

**Implication for adopters:** graphs that expect a scalar or array
root input (rather than an object) are NOT supported in v0. This
covers the common case (graph receives a JSON object describing the
task) and excludes the long tail.

**Why not richer schemas:** NCP graph manifests have NO `input_schema`
field today. Generating something more specific than `{ "type":
"object" }` would either (a) require a new manifest field for graph
authors to declare, or (b) try to infer a schema from sample inputs
(misleading — samples don't represent the full input space).

**Future evolution path:**
- Add a graph-level `input_schema` declaration in the NCP manifest spec.
- The adapter would surface that schema in `tools/list` responses.
- Optionally: an adapter-config "wrapping mode" that lets adopters
  declare per-graph schema overrides without modifying the manifest.

That work waits for real adopter feedback. Honest v0 framing now beats
speculative schema design.

---

## 5. Tool call response shape

**v0 uses the MCP-native `structuredContent` field + text content
mirror** (per spec — `structuredContent` is the typed channel,
`content` is the backwards-compat unstructured channel).

### Terminal-result mapping

`RuntimeContext::execute()` returns an `ExecutionReport` containing
`Vec<TerminalResult>`. The MCP adapter preserves that structure — a
graph with multiple terminal nodes produces multiple terminal results,
and the adapter must surface all of them faithfully (not collapse to
the first).

`structuredContent` always includes:

- **`terminal_results`**: array of terminal results in runtime order.
  Each entry carries `node_id`, `brick_id`, `step`, `result_type`,
  plus `output` (Success/LowConfidence only) and `error`
  (LowConfidence/Failure only).
- **`output_json`**: convenience field for the common single-terminal
  case.

`output_json` derivation rules:

| Terminal results | `output_json` value |
|---|---|
| exactly one Success or LowConfidence terminal | that terminal's output JSON |
| multiple terminal results (any mix) | `{ "terminal_results": [...] }` (same array as above, restated for clients that only read `output_json`) |
| Failure-only (no Success / LowConfidence in any terminal) | `null` |

Clients that need exact fidelity should read `terminal_results`.
`output_json` exists for simple clients and adopter-facing examples
where the common case is one terminal node.

### Top-level `result_type` field

A single `result_type` at the top of `structuredContent` summarizes
the overall outcome across all terminals:

| Terminal mix | Top-level `result_type` | `isError` |
|---|---|---|
| All terminals Success | `"Success"` | `false` |
| At least one LowConfidence, no Failure | `"LowConfidence"` | `true` |
| At least one Failure | `"Failure"` | `true` |

The per-terminal `result_type` in `terminal_results` remains the
authoritative per-node signal; the top-level field is the rolled-up
summary.

### Trace path

`trace_path` is present in every response. When the server was started
without `--trace-dir`, the value is `null`. When `--trace-dir <dir>`
is set, the server canonicalizes the trace directory at startup
(creating it if missing); `trace_path` is therefore an absolute path
to `<canonical-trace-dir>/<trace_id>.jsonl` when emitted. See §11 for
the full trace-dir lifecycle.

### Example — Success (single terminal, common case)

```json
{
  "content": [
    {
      "type": "text",
      "text": "{\"result_type\":\"Success\",\"output_json\":{\"echoed\":\"hi\"},\"trace_id\":\"...\",\"trace_path\":null,\"terminal_results\":[{...}]}"
    }
  ],
  "structuredContent": {
    "result_type": "Success",
    "output_json": { "echoed": "hi" },
    "trace_id": "uuid-v4",
    "trace_path": null,
    "terminal_results": [
      {
        "node_id": "echo_node",
        "brick_id": "org.ncp-examples.echo",
        "step": 0,
        "result_type": "Success",
        "output": { "echoed": "hi" }
      }
    ]
  },
  "isError": false
}
```

### Example — LowConfidence (single terminal)

```json
{
  "structuredContent": {
    "result_type": "LowConfidence",
    "output_json": { "label": "uncertain" },
    "trace_id": "uuid-v4",
    "trace_path": null,
    "terminal_results": [
      {
        "node_id": "classifier",
        "brick_id": "org.ncp.classifier-stub",
        "step": 2,
        "result_type": "LowConfidence",
        "output": { "label": "uncertain" },
        "error": {
          "class": "LOW_CONFIDENCE",
          "message": "confidence 0.43 < threshold 0.7"
        }
      }
    ]
  },
  "isError": true
}
```

### Example — Failure-only (single terminal, no usable output)

```json
{
  "structuredContent": {
    "result_type": "Failure",
    "output_json": null,
    "trace_id": "uuid-v4",
    "trace_path": null,
    "terminal_results": [
      {
        "node_id": "trap_node",
        "brick_id": "org.ncp.trap",
        "step": 1,
        "result_type": "Failure",
        "error": {
          "class": "COMPUTATION_ERROR",
          "message": "brick trapped"
        }
      }
    ]
  },
  "isError": true
}
```

### Example — Multiple terminals (fan-out)

```json
{
  "structuredContent": {
    "result_type": "LowConfidence",
    "output_json": {
      "terminal_results": [
        { "node_id": "a_done", "brick_id": "...", "step": 3, "result_type": "Success", "output": {...} },
        { "node_id": "b_done", "brick_id": "...", "step": 4, "result_type": "LowConfidence", "output": {...}, "error": {...} }
      ]
    },
    "trace_id": "uuid-v4",
    "trace_path": null,
    "terminal_results": [
      { "node_id": "a_done", "brick_id": "...", "step": 3, "result_type": "Success", "output": {...} },
      { "node_id": "b_done", "brick_id": "...", "step": 4, "result_type": "LowConfidence", "output": {...}, "error": {...} }
    ]
  },
  "isError": true
}
```

**Text content mirror.** The `content` array (omitted in the
LowConfidence / Failure / multi-terminal examples above for
readability) is always present and always contains a single text item
whose body is the JSON serialization of `structuredContent`. This is
the backwards-compat channel for MCP clients that don't read
`structuredContent` yet.

**Future:** when the adapter learns to declare per-tool `outputSchema`
(MCP spec supports this), `structuredContent` becomes formally typed
and clients can validate against the declared shape. v0 leaves
`outputSchema` unset — every tool returns the same structural shape
described above, regardless of graph.

**Implementation note (from rmcp v1.7 verification):** the Rust field
names are snake_case (`structured_content`, `is_error`) and serde
renames them to camelCase on the wire (`structuredContent`, `isError`).
The wire shape matches the spec.

---

## 6. Error mapping — protocol-error vs tool-result truth table

This is the most critical protocol distinction and the easiest to get
wrong. Two error classes exist; they MUST NOT be conflated.

### Class A — Graph-execution outcomes (NOT protocol errors)

The graph ran. The runtime produced an `ExecutionReport`. Whether the
rolled-up result is Success, LowConfidence, or Failure (per §5's
top-level rules), the JSON-RPC `tools/call` response is a SUCCESSFUL
response carrying a `CallToolResult`. The `isError` field on the
result distinguishes Success vs error-shaped outcomes within a
successful protocol response.

| Rolled-up outcome | JSON-RPC response | `isError` |
|---|---|---|
| Success (all terminals Success) | tools/call success | `false` |
| LowConfidence (at least one LowConfidence, no Failure) | tools/call success | `true` |
| Failure (at least one Failure terminal) | tools/call success | `true` |

**Why:** MCP clients distinguish "the tool ran but reported a failure
outcome" (semantically a successful call with `isError: true`) from
"the protocol or server fell over" (a JSON-RPC error). Both UI flows
exist on the client side; conflating them breaks adopter
expectations.

### Class B — Protocol / server / infrastructure errors

The protocol itself or the server-side infrastructure failed BEFORE
the graph could produce a result. These get JSON-RPC error responses
(not `CallToolResult`).

| Condition | Response |
|---|---|
| Malformed JSON-RPC request (parse error, wrong shape) | JSON-RPC error |
| Unknown method (e.g. `resources/list` when v0 only supports tools) | JSON-RPC `MethodNotFound` |
| `tools/call` for an unknown tool name | JSON-RPC error |
| Invalid arguments (future, when input schemas exist) | JSON-RPC error |
| Server panic mid-handler | JSON-RPC error (or server crash if unrecoverable) |
| Graph-load failure at startup | startup failure (process exits non-zero); never reaches RPC layer |
| Trace-setup failure BEFORE graph execution begins | JSON-RPC error on the tools/call |

### Class C — Hybrid case: trace-write failure mid-execution (PR C implementation requirement)

A specific edge case: the graph executed and produced a valid
`ExecutionReport`, but writing the trace file failed mid-way (disk
full, permission revoked, etc).

**Implementation requirement for PR C:** implement a trace sink that
records trace-write failures *without* aborting graph execution after
execution has started. If the graph returns a valid result but trace
writing failed, the adapter:

- Returns a SUCCESSFUL `tools/call` response containing the valid
  graph result.
- Sets `isError: true`.
- Adds a `trace_error` field to `structuredContent` describing what
  went wrong.

```json
{
  "structuredContent": {
    "result_type": "Success",
    "output_json": { ... },
    "trace_id": "...",
    "trace_path": "/path/to/incomplete.jsonl",
    "terminal_results": [ ... ],
    "trace_error": {
      "class": "TRACE_WRITE_FAILED",
      "message": "..."
    }
  },
  "isError": true
}
```

This is the only case where `result_type: "Success"` ships alongside
`isError: true`. The `trace_error` field's presence is the
disambiguator.

**If the existing `TraceSink` implementation propagates errors out of
`execute()`,** PR C wraps it in a non-failing adapter shim that
captures the trace error into a side channel and resumes the
execution flow. That shim is the PR C implementation deliverable for
this case.

---

## 7. Logging discipline

**Hard rule from the MCP stdio spec:** stdout MUST contain only valid
JSON-RPC messages. Any other byte written to stdout corrupts the
protocol stream and breaks the connection.

**The discipline:**
- ALL diagnostics, logs, startup messages, and errors → stderr.
- Stdout → reserved exclusively for JSON-RPC protocol traffic
  written by rmcp's transport layer.
- The application code never writes to stdout directly.

**Enforcement (PR C):**

Add `#![deny(clippy::print_stdout)]` to BOTH crate roots:
- `crates/ncp-mcp-server/src/lib.rs`
- `crates/ncp-mcp-server/src/main.rs`

The binary and library are separate Rust compilation units; a deny
attribute on lib.rs does NOT protect main.rs. Even though main.rs is
intentionally a thin entrypoint, the invariant should be explicit at
both crate roots so any future code added to main.rs is automatically
protected.

**Process-level verification (PR C):**

The in-process integration tests verify protocol logic but cannot
catch stdout pollution (the test plumbing is its own byte channel,
not the real subprocess stdout). PR C adds a separate process-level
test that:
1. Spawns the actual built binary as a subprocess (using
   `env!("CARGO_BIN_EXE_ncp-mcp-server")` to avoid hardcoded
   `target/debug/...` paths that break under Windows `.exe` suffix,
   release profile, or custom `$CARGO_TARGET_DIR`).
2. Writes `initialize` / `tools/list` / `tools/call` JSON-RPC frames
   to stdin.
3. Reads stdout line-by-line and asserts every line parses as valid
   JSON-RPC. Any non-JSON-RPC line = test failure.
4. Allows stderr to contain arbitrary log output without assertion.

This is the highest-leverage test in the entire phase. Stdout
pollution is the failure mode most likely to ship silently if not
explicitly guarded.

---

## 8. Workspace structure

**Locked decision: separate workspace crate at `crates/ncp-mcp-server/`.**

The runtime crate (`runtime/`) ships at `ncp-runtime` on crates.io and
is intentionally lean — adopters who embed `RuntimeContext` directly
in their own Rust services shouldn't pay for adapter deps they don't
use.

The MCP adapter pulls in `tokio` (multi-thread runtime), `rmcp`
(including `schemars`, `async-trait`, `futures`), and their transitive
deps — measured at **55 unique transitive crates** during the SDK
verification (see §10). That's the right cost for a server that has
to do real protocol work; it's the wrong cost to push onto every
`cargo install ncp-runtime` user.

**Layout:**

```
ncp/
├── runtime/                          # unchanged; ncp-runtime crate
│   └── ...
├── crates/                           # NEW directory for adapter crates
│   └── ncp-mcp-server/               # this phase
│       ├── Cargo.toml
│       ├── README.md
│       ├── CHANGELOG.md              # crate-local (PR E)
│       └── src/
│           ├── lib.rs
│           ├── main.rs
│           ├── cli.rs
│           ├── naming.rs
│           ├── send_sync_check.rs
│           └── server.rs              # PR C
└── ...
```

The `runtime/` directory stays where it is — no churn. The `crates/`
directory is the new home for adapter crates. Future Phase 3A.3/3A.4
work adds `crates/ncp-langgraph/`, `crates/ncp-python-sdk/`, etc.,
under the same pattern.

**Rejected alternatives (and why):**

1. **Third binary inside `runtime/` crate** (`runtime/src/bin/ncp-mcp-server.rs`).
   Simplest layout — one crate, three binaries. Rejected: pulls tokio
   + rmcp + 53 other transitive deps into the published `ncp-runtime`
   crate. Every `cargo install ncp-runtime` user pays that cost,
   whether they touch MCP or not. After the v0.3.4–v0.3.6 polish work
   to keep `ncp-runtime` lean and shapely on crates.io, this would be
   a clear regression.

2. **Feature-gated binary inside `runtime/` crate** (gated behind a
   `mcp-server` Cargo feature). Same physical location, opt-in deps.
   Rejected: feature/dependency interactions get subtle (does the
   default-features for `ncp-runtime`'s lib consumers behave
   correctly? does docs.rs build with the feature on or off, and what
   gets rendered?). The install story also gets less clear (`cargo
   install ncp-runtime --features mcp-server --bin ncp-mcp-server`
   vs the clean `cargo install ncp-mcp-server`).

3. **Defer the workspace-layout decision to PR A's design doc.**
   Rejected: crate placement drives every following implementation
   PR (Cargo.toml, dep declarations, test scaffolding, install
   instructions, release ceremony). Locking it now lets PRs B–E
   execute against a stable foundation.

---

## 9. `ncp-runtime` dependency pattern (version + path together)

The adapter depends on `ncp-runtime` via:

```toml
[dependencies]
ncp-runtime = { version = "0.3.6", path = "../../runtime" }
```

**Both fields are intentional.** During workspace development, Cargo
prefers the `path` dep — the adapter always compiles against the
local `runtime/` crate, so any runtime change is immediately exercised
by the adapter's tests. When `ncp-mcp-server` publishes to crates.io,
Cargo uses the `version` requirement (the `path` is ignored by
published crates, since the path doesn't exist on consumer machines).

**Rejected alternatives:**

- **Path only** (`{ path = "../../runtime" }`). Cannot publish — Cargo
  rejects path-only deps in published crates.
- **Version only** (`"0.3.6"`). Loses local-runtime testing during
  development; every adapter change has to test against a published
  runtime version, defeating workspace ergonomics.
- **Path during dev, swap to version at publish time.** Forces a
  manual swap in every release PR; easy to forget; pollutes the diff
  with non-functional churn.

The version+path pattern is the standard Cargo workspace idiom for
adapters that depend on other workspace members.

**Bump policy at adapter release:** PR E checks whether `ncp-runtime`
released a new version between PR B (initial pin) and PR E (adapter
publish). If yes, PR E bumps the version requirement to match the
current `ncp-runtime` release. The `path` field stays put either way.

---

## 10. MCP SDK choice — `rmcp v1.7` (verified)

**Decision: lock `rmcp v1.7.x`** as the MCP SDK, with these features:

```toml
rmcp = { version = "1.7", default-features = false, features = ["server", "transport-io"] }
```

`default-features = false` excludes the `macros` feature (which pulls
in `rmcp-macros` and `pastey` for compile-time tool declaration —
incompatible with our runtime-loaded graph requirement). The `server`
feature provides the `ServerHandler` trait + supporting infrastructure;
`transport-io` provides the stdio transport.

### Verification matrix results

Each cell empirically verified by building a throwaway crate
(`rmcp-verify/`) before PR A drafting. Results:

| # | Cell | Verdict | Evidence |
|---|---|---|---|
| 1 | Crate name + features | ✅ pass | Above Cargo.toml clause builds clean. Features excluded: `macros`, `client`, `auth*`, all reqwest variants. |
| 2 | Minimal stdio server example compiles | ✅ pass | `cargo check` exits 0 with zero warnings after fixing two deprecation warnings (see implementation notes below). |
| 3 | Dependency tree size | ⚠ acceptable | **55 unique transitive crates** (`cargo tree --prefix none \| sort -u`). Includes tokio (multi-thread + io-std + macros), serde + serde_json, schemars, async-trait, futures, anyhow + procs. Non-trivial but unavoidable for a tokio MCP server — and justifies the §8 separate-crate decision. |
| 4 | Stdout/stderr behavior | ✅ pass (at API level) | `eprintln!` cleanly to stderr in the throwaway; `rmcp::transport::stdio()` owns stdin/stdout exclusively for protocol. Full process-level test under JSON-RPC load happens in PR C (§7). |
| 5 | Compatibility with response shape (structuredContent + text mirror) | ✅ pass | Sample `CallToolResult` serialization produced exactly the MCP-spec-compliant wire shape: `{ "content": [{"type":"text","text":"..."}], "structuredContent": {...}, "isError": false }`. |
| 6 | Dynamic tool registration without macros | ✅ pass | Manual `ServerHandler` impl with runtime-constructed `Vec<Tool>` compiles and dispatches. Zero `#[tool]` / `#[tool_router]` / `#[tool_handler]` macros used. `list_tools()` returns the runtime vector; `call_tool()` dispatches by name. |

### Implementation notes from verification

These are the gotchas captured during the throwaway crate work —
documented here so PR B/C don't re-discover them.

**Type-name deprecations.** rmcp v1.7 deprecated several singular-form
type aliases in favor of plural-form. Use:

| Use | Don't use (deprecated) |
|---|---|
| `CallToolRequestParams` | `CallToolRequestParam` |
| `PaginatedRequestParams` | `PaginatedRequestParam` |
| `rmcp::ErrorData` | `rmcp::Error` |

**Non-exhaustive structs.** `Tool`, `ServerInfo`, `ListToolsResult`,
and `CallToolResult` are all `#[non_exhaustive]`. From outside the
defining crate, you cannot construct these via struct-literal syntax
even with `..Default::default()`. The pattern that compiles:

```rust
let mut tool = Tool::default();
tool.name = "echo".into();
tool.description = Some("Echo input back".into());
tool.input_schema = Arc::new(schema_obj);
```

`Tool` derives `Default`, so `Tool::default()` gives a baseline; field
mutation then sets the fields we care about. Same pattern for
`ServerInfo`, `ListToolsResult`, `CallToolResult`.

**Field-name serde renames.** Rust field names are snake_case; serde
renames to camelCase on the wire to match the MCP spec:

| Rust struct field | JSON wire field |
|---|---|
| `structured_content` | `structuredContent` |
| `is_error` | `isError` |
| `input_schema` | `inputSchema` |
| `output_schema` | `outputSchema` |

**Tokio features needed:** `rt-multi-thread` (the async runtime),
`macros` (the `#[tokio::main]` attribute), `io-std` (stdin/stdout
access for rmcp's `transport-io`).

**SDK version pin.** `rmcp = "1.7"` is a semver requirement (caret
implied — picks any 1.x ≥ 1.7, blocks 2.0), NOT an exact pin. The
workspace `Cargo.lock` pins the exact SDK version for repo CI and
release builds. PR E MUST verify via `cargo package -p ncp-mcp-server
--list` that `Cargo.lock` is included in the published `.crate`;
otherwise, do not document `cargo install ncp-mcp-server --locked` as
a reproducible install path until the package layout is fixed.
Revisit the SDK pin at each MCP spec release.

### Fallback path (if `rmcp` ever blocks)

If a future `rmcp` change breaks one of the matrix cells (most
plausibly cell 6 — dynamic dispatch), the fallback order is:

1. `mcp-protocol-sdk` (crates.io) — claims MCP 2025-06-18 compliance.
2. `tmcp` (crates.io) — lighter, also actively maintained.
3. Hand-rolled JSON-RPC over stdio (~500–1000 lines for basic server).

Re-run the 6-cell matrix against the fallback if invoked. Update §10
of this doc with new results and rationale.

---

## 11. Async bridging: tokio MCP server ↔ sync `RuntimeContext::execute()`

**The problem.** `rmcp` is tokio-based — its `ServerHandler` methods
(`list_tools`, `call_tool`) are `async fn`. NCP's
`RuntimeContext::execute()` is sync-blocking — it drives wasmtime
serially in the calling thread. Calling sync-blocking code directly
from inside an async handler would block the entire tokio runtime
thread, freezing other concurrent tool calls.

**Default pattern: `tokio::task::spawn_blocking`.**

Inside `call_tool`, wrap the `execute()` call. The shape (subject to
final `rmcp` API surface; semantic intent is what's locked):

```
async fn call_tool(...) -> Result<CallToolResult, McpError> {
    let ctx = self.runtime_context_for(&request.name)?; // Arc<RuntimeContext>
    let input = convert_mcp_arguments(&request.arguments)?; // per §4 conversion table

    let report = tokio::task::spawn_blocking(move || {
        let mut tracer = ...;
        let mut hooks = ExecuteHooks::default();
        let opts = ExecuteOptions::default();
        ctx.execute(&input, &mut tracer, &mut hooks, &opts)
    })
    .await
    .map_err(|join_err| /* convert join error to McpError */)??;

    Ok(build_call_tool_result(&report)) // per §5
}
```

`spawn_blocking` runs the closure on tokio's blocking-task thread
pool, leaving the async runtime threads free to handle other RPC
traffic. The `.await` on the JoinHandle yields back to the async
runtime until the blocking task finishes.

**Requires `RuntimeContext: Send + Sync`.** The closure passed to
`spawn_blocking` must be `Send + 'static`; it captures the
`Arc<RuntimeContext>`, so `RuntimeContext` must be `Send + Sync` for
the Arc to be `Send`.

**PR B verifies this with a compile-time assertion** (`send_sync_check.rs`):

```rust
fn assert_send_sync<T: Send + Sync>() {}

#[allow(dead_code)]
fn _runtime_context_must_be_send_sync() {
    assert_send_sync::<ncp_runtime::RuntimeContext>();
}
```

If `RuntimeContext` is `Send + Sync`, this compiles and we use the
`spawn_blocking` pattern in PR C. If it isn't, PR B catches it at
compile time — long before PR C's protocol code is written.

### Fallback: worker-thread architecture (if Send+Sync fails)

If `RuntimeContext` is not `Send + Sync`, `spawn_blocking` is not
viable and the design changes shape. The fallback pattern:

```
┌────────────────────────────────────────────────────────────┐
│ tokio runtime (rmcp ServerHandler)                         │
│                                                            │
│   call_tool(request) ─send(job)─►──┐                       │
│                                    │                       │
│   wait for result ◄──recv(result)──┤                       │
└────────────────────────────────────┼───────────────────────┘
                                     │ mpsc channel
                                     ▼
                  ┌──────────────────────────────────┐
                  │ worker thread (single owner)     │
                  │   owns: HashMap<ToolName, Ctx>   │
                  │   loop: recv job, execute,       │
                  │         send back result         │
                  └──────────────────────────────────┘
```

A single dedicated worker thread owns the `HashMap<ToolName,
RuntimeContext>`. If this fallback is selected, graph loading also
happens inside the worker thread: the async side sends validated graph
paths/config to the worker at startup, and the worker constructs the
`RuntimeContext`s locally. No `RuntimeContext` ever crosses a thread
boundary, so `Send + Sync` is not required for the contexts.

Async handlers send jobs over an mpsc channel and `.await` the result
on a oneshot reply channel.

Trade-offs:
- Serializes all graph execution (no concurrency benefit on the
  worker side).
- Adds latency from channel handoff (microseconds; negligible vs
  graph execution times).
- More code to maintain.

**Acceptable** as a v0 fallback because v0 doesn't promise concurrent
tool calls under load; the headline value is single-tool ergonomics.

PR A documents both patterns; PR B's compile assertion picks the path;
PR C implements whichever PR B selected.

### PR C concurrent-call test requirement (non-negotiable for v0)

`Send + Sync` proves memory safety, not behavioral correctness under
concurrent load. PR C MUST include a concurrent-call integration
test:

- Issue at least two simultaneous `tools/call` requests against the
  same loaded graph.
- Verify both complete successfully.
- Verify both produce distinct `trace_id` values.
- Verify trace files do not collide when `--trace-dir` is enabled
  (each call writes to its own `<trace_id>.jsonl` per §12).

This test is non-negotiable for v0. The worst-case behavioral bugs
here (trace_id collision under concurrent allocation, lost results
under channel handoff, deadlock between spawn_blocking and async
handler) are impossible to catch via single-call tests. If we ship
v0 without this test, we're shipping behavioral correctness on faith.

The test applies equally to the spawn_blocking path AND the
worker-thread fallback path — under the worker-thread fallback, the
worker serializes execution but the test still verifies that the
serialization is correct (both requests get distinct trace_ids,
both complete, no result loss).

---

## 12. Tracing — `--trace-dir <dir>` semantics

**Flag shape:** `--trace-dir <dir>` (singular path; NOT `--trace
<path>`). Per-call trace files live inside the directory.

### Startup behavior

1. Parse `--trace-dir <dir>` from CLI.
2. If absent: use `NullTrace` for all calls; `trace_path` field is
   `null` in every response. Done.
3. If present:
   - If `<dir>` exists and is NOT a directory (regular file, symlink
     to non-dir, etc): **startup failure** with clear error.
   - If `<dir>` exists and is a directory: proceed.
   - If `<dir>` does not exist: server creates it (recursive
     `fs::create_dir_all`).
   - **Canonicalize** the path (`std::fs::canonicalize`). All
     `trace_path` values in responses are absolute paths derived from
     the canonical form. Adopters can rely on `trace_path` being
     absolute.

### Per-call behavior

For each `tools/call` invocation:
- Allocate a new `trace_id` (UUID v4) at the start of the call.
- Open `<canonical-trace-dir>/<trace_id>.jsonl` with
  exclusive-create semantics (`OpenOptions::new().write(true).create_new(true)`)
  where the OS supports it. Filename collision is statistically
  impossible (UUID v4) but `create_new` makes any collision a hard
  failure rather than silent overwrite.
- Stream JSONL trace records as the graph executes (same format as
  the existing `runtime/src/trace.rs` machinery — the adapter wraps
  it, doesn't reinvent).
- On success: `trace_path` field in the response is the absolute
  path to the file.
- On write failure mid-execution: see §6 Class C (PR C builds the
  non-failing trace shim).

### Why per-call files (not a single shared file)

Multi-graph + concurrent tool calls would race on a single
`--trace <path>` file. Per-call files give:
- No lock contention.
- No append-order ambiguity.
- Trivial cleanup (rm a file = drop one call's trace).
- Clean correspondence: one `trace_id` ↔ one file.

The trade-off is many small files in the trace dir over time.
Adopters who want long-term aggregation can run a sidecar process
that concatenates / archives / ships them off — outside the
adapter's responsibility.

---

## 13. `tools` capability `listChanged: false`

In the `initialize` response, the server declares its capabilities.
For v0:

- The server declares the `tools` capability.
- The `listChanged` field within the `tools` capability is either
  OMITTED or set to `false`.

Implementation code may vary depending on the final `rmcp` builder
API surface, but the wire-level requirement is fixed: the server
either omits `listChanged` or sends it as `false`. PR C verifies via
the process-level stdio test (§7) that the actual wire response
matches this contract.

**Why:** the `listChanged` field on the `tools` capability tells the
client whether the server will emit
`notifications/tools/list_changed` events. v0's tool list is **static
after startup** — the set of `--graph` flags is fixed at process
launch; no hot-reload, no dynamic tool addition/removal during the
server's lifetime. Setting `listChanged: true` would be a spec
violation (the server would declare a capability it doesn't actually
fulfill).

**Future:** if the adapter ever gains hot-reload (`--graph-watch`
or similar), `listChanged` flips to `true` AND the server starts
emitting `notifications/tools/list_changed` whenever the loaded
graph set changes. Both halves of that change ship together —
the capability flag without the notification emission is a
spec-noncompliance bug.

---

## 14. MCP spec version targeted

**Targeted spec version: MCP 2025-11-25** (the current stable at
PR A drafting time).

`rmcp v1.7` implements this spec version. The version is documented
here and the SDK is pinned via the workspace `Cargo.lock` (per §10's
pinning discipline; PR E verifies `Cargo.lock` is actually included
in the published `.crate` before documenting `cargo install --locked`
as a reproducible install path).

**Spec-version evolution policy:**

- When a new MCP spec version ships, this doc gets an explicit pass
  to update the targeted version + survey what changed.
- SDK upgrade follows: bump the `rmcp` requirement in
  `crates/ncp-mcp-server/Cargo.toml` to the matching version, run
  the full test suite, run the §7 process-level stdio test, run a
  manual smoke against at least one real MCP host.
- Any behavior change visible to adopters (response shape, error
  semantics, etc.) ships under a new adapter version with a
  CHANGELOG entry naming the spec version transition.

**Historical context:** the MCP spec has shipped 2024-11-05,
2025-06-18, and 2025-11-25 in roughly six-month cadence. The
transport layer changed once (HTTP+SSE → Streamable HTTP) at
2025-06-18; stdio has been stable across all three versions. v0 of
the adapter targets only stdio, so transport churn is not a v0
concern.

---

## 15. Versioning policy (adapter decoupled from runtime)

`ncp-mcp-server` is version-decoupled from `ncp-runtime`.

**Initial version:** `v0.1.0`. Reflects new-adapter maturity, not the
same maturity bar as the runtime. The leading-zero major version is
honest about API stability — the Rust module API for the adapter
crate is treated as internal until adopter feedback shapes a stable
public surface.

**`ncp-runtime` version is NOT touched by this phase.** The runtime
crate's public API is unchanged: no new types added, no signatures
modified, no behavior changes. The adapter consumes the existing
`RuntimeContext::load()` / `RuntimeContext::execute()` API as
documented in `docs/ADOPTION_GUIDE.md` §6. Bumping `ncp-runtime` for
this phase would imply a runtime change that doesn't exist.

**Adapter publish is its OWN release ceremony.** When PR E publishes
`ncp-mcp-server v0.1.0`, that ceremony does NOT involve a
`ncp-runtime` tag, GitHub Release, GHCR build, or Zenodo archive.
The runtime and adapter are independent publishing tracks.

### Tag convention (locked, critical to avoid cross-crate workflow collisions)

The existing `release.yml` and `docker.yml` workflows trigger on
`push: tags: ['v*']`. Any tag starting with `v` fires both — meaning
an adapter tag like `v0.1.0-mcp` would (a) build a GitHub Release
labeled with the adapter version but containing RUNTIME binaries
(broken provenance), and (b) waste CI on a Docker workflow whose
semver regex would reject the malformed tag.

**Adapter tags MUST NOT start with `v`:**

| Crate | Tag pattern | Workflows that fire on push |
|---|---|---|
| `ncp-runtime` | `v0.3.6`, `v0.3.7-rc.1`, etc. | `release.yml` + `docker.yml` (correct — runtime release) |
| `ncp-mcp-server` | `ncp-mcp-server-v0.1.0`, `ncp-mcp-server-v0.1.0-rc.1` | **None** (correct — adapter is crates.io-first for v0.1.0) |
| Future adapter crates | `ncp-langgraph-v0.1.0`, etc. | None (same pattern) |

This follows a common multi-crate workspace tagging pattern. The
plan's release ceremony for PR E includes an explicit verification
step: after pushing an adapter tag, `gh run list` should show NO
Release or Docker workflow runs triggered by it. If runtime workflows
DO fire, the tag pattern was wrong — abort, delete tag, fix pattern.

### v0.1.0 → next-version policy

When the adapter's next version ships (whether v0.1.1, v0.2.0, or
later), `docs/MCP_ADAPTER.md` is the source of truth for any
adopter-visible change. The CHANGELOG entry (per §16) cross-references
this doc's section that changed.

---

## 16. CHANGELOG strategy (crate-local)

Adapter version history lives in `crates/ncp-mcp-server/CHANGELOG.md`,
not in the root `CHANGELOG.md`.

**Rationale:**

- Root `CHANGELOG.md` tracks `ncp-runtime` version history (0.3.x
  today, evolving forward).
- Adapter version history (`ncp-mcp-server` 0.1.x) is on a separate
  track per §15.
- Mixing both in one linear history would conflate two independent
  release cadences and confuse readers trying to answer "what
  changed in v0.3.7 of the runtime?" or "what changed in v0.1.1 of
  the adapter?"

**Format:** [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
matching the root CHANGELOG convention. Sections in standard order
(Added / Fixed / Changed / Removed / Deprecated / Security).

**Root CHANGELOG.md MAY** mention phase milestones at the project
level (e.g., "Phase 3A.2 — MCP Adapter complete (ncp-mcp-server v0.1.0
published)"), but adapter-specific version entries belong only in
the crate-local file.

**Future adapter crates** (Phase 3A.3 LangGraph wrapper, Phase 3A.4
SDKs, etc.) follow the same pattern: each adapter crate carries its
own crate-local CHANGELOG.

---

## 17. Repo-hygiene: no specific MCP-host product names in tracked docs

**Rule:** tracked markdown in this repo MUST NOT use specific
vendor/product names as examples of MCP-compatible hosts. Use generic
MCP terminology instead.

| Don't write | Write instead |
|---|---|
| a specific desktop-host product name | "MCP-compatible desktop hosts" |
| a specific CLI-host product name | "MCP-compatible CLI hosts" |
| "<host> users can..." | "MCP client users can..." |

**Why this rule exists:** during Phase 3A.1, GitHub's repository
homepage contributors widget falsely attributed a third-party account
as a contributor after tracked roadmap text mentioned a same-named
product. That account had no commits, PRs, issues, comments, reviews,
stars, or collaborator relationship with this repository. PR K removed
the trigger text. This section locks the prevention rule for future
MCP-adapter docs.

**Tracked docs should stay generic.** Specific host names may be useful
in PR descriptions, issue comments, private planning notes, or external
blog/tutorial material, but committed repository documentation should
prefer product-family terms: "MCP host", "MCP client",
"MCP-compatible desktop host", and "MCP-compatible CLI host".

**Reasonable exception:** if a future compatibility matrix is created,
specific host names may be listed there only after we intentionally
accept the attribution risk and add a matching verification/check. Until
then, generic terminology is the default.
