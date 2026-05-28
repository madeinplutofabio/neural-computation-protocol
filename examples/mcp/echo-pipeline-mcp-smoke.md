<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# Manual smoke test — `org.ncp-examples.echo-pipeline`

End-to-end recipe for verifying that the built `ncp-mcp-server`
binary speaks JSON-RPC correctly on your machine. Run this before
wiring the adapter into a real MCP-compatible host.

For an automated version of the same dialog, run
[the CI smoke script](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/examples/mcp/ci_smoke.py)
instead.

---

## 1. Build the binary

From the workspace root:

```bash
cargo build -p ncp-mcp-server --release --locked
```

The binary lands at `target/release/ncp-mcp-server`
(`target\release\ncp-mcp-server.exe` on Windows).

---

## 2. Launch the server against the echo graph

In one terminal, with the workspace root as the working directory:

```bash
./target/release/ncp-mcp-server \
  --graph examples/graphs/echo-pipeline/graph.yaml \
  --brick-dir examples/bricks \
  --trace-dir /tmp/ncp-mcp-smoke
```

The server writes a one-line summary to **stderr**:

```
loaded 1 graph(s) as MCP tools: org.ncp-examples.echo-pipeline
```

…and then blocks on stdin, waiting for JSON-RPC frames. **Stdout
stays empty** until you send a request. That's the §7 stdout
discipline at work.

If you'd prefer to drive the server without typing JSON frames into a
running process, see "Driving from the same terminal (Unix / macOS)"
below.

---

## 3. Send the JSON-RPC dialog

Each frame below is **one line of JSON terminated by `\n`**. The
server reads one frame at a time from stdin.

### 3.1 `initialize`

Send:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"manual-smoke","version":"0.0.0"}}}
```

Expect on stdout (formatted for readability — the wire frame is one line):

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "protocolVersion": "2025-11-25",
    "capabilities": {
      "tools": { "listChanged": false }
    },
    "serverInfo": {
      "name": "ncp-mcp-server",
      "version": "0.1.0"
    }
  }
}
```

Key fields to confirm:

- `result.capabilities.tools` is present (the server advertises the
  tools capability).
- `result.capabilities.tools.listChanged` is `false` (per §13).
- `result.serverInfo.name` is `ncp-mcp-server` (NOT `rmcp` — see §10
  for the `Implementation::new(env!(...))` rationale).

### 3.2 `notifications/initialized`

Send (no response expected — this is a notification, not a request):

```json
{"jsonrpc":"2.0","method":"notifications/initialized"}
```

### 3.3 `tools/list`

Send:

```json
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
```

Expect:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "tools": [
      {
        "name": "org.ncp-examples.echo-pipeline",
        "description": "NCP graph org.ncp-examples.echo-pipeline (<graph_version>). Object-shaped arguments are passed verbatim as the graph root input.",
        "inputSchema": { "type": "object" }
      }
    ]
  }
}
```

Key fields to confirm:

- Exactly one tool, named `org.ncp-examples.echo-pipeline` (per §3
  derivation).
- `inputSchema.type` is `"object"` (per §4 v0 schema).
- `description` includes the graph_id and a `(<graph_version>)`
  marker — the exact version string comes from the
  `graph_version` field of the loaded manifest.

### 3.4 `tools/call`

Send (with any object-shaped arguments — the echo brick passes them
through):

```json
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"org.ncp-examples.echo-pipeline","arguments":{"hello":"world"}}}
```

Expect a Class A success response:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "content": [
      { "type": "text", "text": "<JSON serialization of structuredContent>" }
    ],
    "structuredContent": {
      "result_type": "Success",
      "output_json": { ... },
      "trace_id": "<uuid>",
      "trace_path": "/tmp/ncp-mcp-smoke/<trace_id>.jsonl",
      "terminal_results": [ ... ]
    },
    "isError": false
  }
}
```

Key fields to confirm:

- `result.isError` is `false` (Class A Success per §5/§6).
- `result.structuredContent.result_type` is `"Success"`.
- `result.structuredContent.trace_path` points at a real `.jsonl`
  file inside the `--trace-dir`.
- `result.structuredContent.terminal_results` is a non-empty array.
  An empty array would trigger the adapter's defensive
  `NO_TERMINAL_RESULTS` Failure path — the echo graph should always
  produce at least one terminal.
- `result.content[0].text` is a JSON string whose parsed value
  equals `result.structuredContent` exactly (text mirror per §5).

---

## 4. Inspect the trace file

The adapter wrote a trace for that call. Inspect it:

```bash
cat /tmp/ncp-mcp-smoke/<trace_id>.jsonl
```

Expected: one `runtime_info` record at the top, followed by one
`invoke` record per brick step. Schema reference: the
[NCP protocol spec](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/spec/ncp-v0.2.3.md).

---

## 5. Shut the server down

Send `Ctrl+D` (Unix / macOS) or `Ctrl+Z` followed by `Enter`
(Windows) to close stdin. The server exits cleanly when stdin
reaches EOF.

---

## Driving from the same terminal (Unix / macOS)

If your shell makes it inconvenient to type JSON frames into a
running process, drive the server with a bash here-doc instead:

```bash
./target/release/ncp-mcp-server \
  --graph examples/graphs/echo-pipeline/graph.yaml \
  --brick-dir examples/bricks \
  --trace-dir /tmp/ncp-mcp-smoke <<'EOF'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"manual-smoke","version":"0.0.0"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"org.ncp-examples.echo-pipeline","arguments":{"hello":"world"}}}
EOF
```

The server reads all four frames in one pass, emits three responses
(the three requests; the notification has no response), then exits
when it sees the here-doc's EOF.

> **Windows note:** the bash here-doc syntax above is Unix / macOS
> shell only. On Windows, either drive the server interactively
> through the steps above OR run
> [the CI smoke script](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/examples/mcp/ci_smoke.py)
> for an automated cross-platform alternative.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `error: trace writer construction failed for <path>` | The `--trace-dir` points at a path that exists but isn't a writable directory. |
| Server exits non-zero on startup with a "failed to load graph" message | The `--graph` argument points at a missing or malformed manifest, or `--brick-dir` doesn't contain the bricks the graph references. Re-run with `--check` to validate without starting the MCP server. |
| `tools/call` returns a JSON-RPC error response (`error` present, `result` absent) | Class B — the tool name doesn't match the derived name in `tools/list`, or the request shape failed validation. Check the response's `error.message`. |
| Response carries `result_type: "Failure"` and `isError: true` for a graph you expected to succeed | Read the per-terminal `error` objects in `structuredContent.terminal_results`. The graph itself returned a Failure terminal; the adapter is faithfully surfacing it (Class A). |
| Output on stdout that isn't JSON-RPC | Bug — stdout is reserved per §7. File an issue with the offending line. |
