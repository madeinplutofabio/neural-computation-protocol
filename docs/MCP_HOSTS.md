<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# MCP host compatibility notes

Validation runs of `ncp-mcp-server` v0.1.0 against real MCP-compatible host
clients beyond the official MCP Python SDK (the SDK is the Phase 3A.2
publish gate and runs continuously via
[`examples/mcp/ci_smoke.py`](../examples/mcp/ci_smoke.py); this document
records external ecosystem validation per issue #28).

Per [`docs/MCP_ADAPTER.md` §17](MCP_ADAPTER.md#17-repo-hygiene-no-specific-mcp-host-product-names-in-tracked-docs),
tracked docs use generic terminology (CLI host A / CLI host B, desktop
host A, etc.) rather than specific vendor product names. This file names
behaviors, not products; concrete host identities live in PR notes and
private test logs.

## Methodology

Each validation attempts three levels, in order:

| Level | Definition | How verified in these runs |
|---|---|---|
| **1. Connection** | Host spawns `ncp-mcp-server` and completes the MCP `initialize` handshake with `serverInfo` + capabilities. | Host's own `mcp get`/`mcp list` health check, or the host's MCP log file showing a successful handshake. |
| **2. Discovery** | Host successfully enumerates the registered NCP graph through `tools/list` or equivalent host tool-discovery output. Capability advertisement alone is insufficient. | Host's log or `tools/list` round-trip visible in event stream, OR direct evidence that the host's tool catalog contains the graph (e.g. the host's tool-name sanitization produces the sanitized form in a diagnostic). |
| **3. Execution** | Host sends a `tools/call` and surfaces the structured response. | A `<trace_id>.jsonl` file appears under `--trace-dir`, AND the host's event stream contains the `tools/call` with `result_type: "Success"` in `structuredContent`. |

Only all-three verified counts as a full **PASS**. Lower confidence is
documented as **PARTIAL** with the specific gap.

Test fixture: [`examples/graphs/echo-pipeline/graph.yaml`](../examples/graphs/echo-pipeline/graph.yaml)
(the established adapter smoke-test fixture). Reproduction instructions
below, built on [`examples/mcp/echo-pipeline-mcp-smoke.md`](../examples/mcp/echo-pipeline-mcp-smoke.md).

## Compatibility matrix (2026-10-24)

| Host | Version | Interface | OS | L1 Connection | L2 Discovery | L3 Execution | Overall |
|---|---|---|---|---|---|---|---|
| CLI host A | 2.1.280 | stdio | Windows 10 Pro | PASS (via protocol-era fallback, see quirk 1) | PASS | PASS | **PASS** |
| CLI host B | 0.163.0-alpha.10.2 | stdio | Windows 10 Pro | PASS | PASS | PASS | **PASS** |

**Server under test:** `ncp-mcp-server` v0.1.0 and `ncp-runtime` v0.3.6,
built from `main` at commit `e04a7a3` using Rust 1.94.0 via the pinned
workspace toolchain, release profile. All runs on Windows 10 Pro
10.0.19045.

**Reading the row.** CLI host A completed the MCP handshake (after one
transparent fallback described in quirk 1 below), enumerated the
registered graph via `tools/list`, invoked it through `tools/call`,
and surfaced `result_type: "Success"` to the caller. The pre-invocation
`claude auth login` refresh was a prerequisite for the automated
subprocess test; a stale OAuth token would otherwise block Level 3.
CLI host B exercised the full `initialize → tools/list → tools/call`
round-trip, received a `Success` result, and the trace file appeared
under `--trace-dir` as expected.

Zero `ncp-mcp-server` defects were observed in either run. The two
host-side quirks documented below required no server-side change.

## Observed quirks

### Quirk 1: CLI host A sends a pre-init `server/discover` probe

CLI host A issues a non-standard JSON-RPC request
(`method: "server/discover"`) **before** the MCP `initialize` request.
`ncp-mcp-server` v0.1.0 rejects this per the MCP spec (initialized
expected) with:

```
expect initialized request, but received:
  Some(Request(JsonRpcRequest { ..., request: CustomRequest(CustomRequest {
    method: "server/discover", params: Some(Object {}), ...
  }) }))
```

The server exits with a stderr error. The host detects the failure and
**respawns the server for a legacy-era handshake**, which succeeds with
`negotiatedProtocolVersion: "2025-11-25"` and
`capabilities: { hasTools: true, ... }`. From the host's own log:

```
version negotiation probe closed the stdio server
(rmcp-class pre-init hard close); respawning pinned legacy
...
Successfully connected (transport: stdio) in 197ms
Connection established with capabilities:
  {"hasTools":true, "serverVersion":{"name":"ncp-mcp-server", ...},
   "protocolEra":"legacy", "negotiatedProtocolVersion":"2025-11-25"}
```

**Impact observed:** no connection failure after fallback, and no
downstream Execution-level impact when the full handshake path was
exercised against this host. The successful fallback connection was
reported as 197 ms in the host's own log; this document does not
establish how much of that latency is attributable to the failed
probe (a direct comparison against a probe-less connection would be
required). The server behaves per the MCP spec; the fallback lives
on the host side.

**Recommended action:** no server change. Document the behavior so
future users running MCP log diagnostics don't mis-attribute the
first-pass error line to a server bug.

### Quirk 2: CLI host A sanitizes dots to underscores in MCP tool names

CLI host A normalizes tool names in its internal tool catalog by
replacing `.` characters with `_`. The server advertises the graph as
`org.ncp-examples.echo-pipeline`, but Host A's tool namespace exposes
it as `mcp__ncp-echo-pipeline__org_ncp-examples_echo-pipeline` (with
underscores in place of the two dots). This matters for:

- `--allowedTools` command-line arguments: pass the sanitized name,
  not the server-advertised name, or the invocation is denied with
  "Permission was denied, so the tool never ran" before the server is
  contacted.
- Prompt text that references the tool by name: fuzzy matching may
  still find it, but exact-match references in automation should use
  the sanitized form.

CLI host B does not perform this sanitization; it exposes the tool
under its exact advertised name `org.ncp-examples.echo-pipeline`.

**Impact observed:** first Execution attempt with the dot-form name in
`--allowedTools` was denied before the server received a `tools/call`.
Reusing the sanitized form in the same session succeeded immediately
and produced the expected trace file. No server change required; the
host's tool-name convention is a documentation item for users wiring
this host up to `ncp-mcp-server`.

### No quirks observed for CLI host B

CLI host B sent a standard MCP `initialize` on the first attempt,
performed `tools/list` to enumerate the registered graph (exposed
under its exact advertised name), and invoked it via `tools/call`
with no retries. The event stream recorded the full structured
response including `trace_path`.

## Reproduction

### Prerequisites

1. Build the server (Rust 1.94+):

   ```bash
   cargo build -p ncp-mcp-server --release --locked
   ```

2. Pick a trace directory, e.g. `C:\Temp\ncp-mcp-hosts` or
   `/tmp/ncp-mcp-hosts`.

3. Have the host CLI installed and authenticated.

### CLI host A (local-scope stdio registration)

In the examples below, `<host-a-cli>` and `<host-b-cli>` are
**placeholders** for your actual CLI executable name (whatever the
host's own `--version` identifies). They are not literal commands.
Replace each placeholder with the installed binary name for the host
you are reproducing against.

Register (adjust absolute paths for your checkout):

```bash
<host-a-cli> mcp add ncp-echo-pipeline --scope local -- \
  /abs/path/to/target/release/ncp-mcp-server \
  --graph /abs/path/to/examples/graphs/echo-pipeline/graph.yaml \
  --brick-dir /abs/path/to/examples/bricks \
  --trace-dir /abs/path/to/trace-dir
```

Verify Level 1 (connection):

```bash
<host-a-cli> mcp get ncp-echo-pipeline
```

Expected: `Status: √ Connected` (or equivalent host-specific wording).
Inspect the host's own MCP log file to observe the quirk 1 fallback
sequence if desired.

Clean up:

```bash
<host-a-cli> mcp remove ncp-echo-pipeline -s local
```

### CLI host B (global-scope stdio registration)

Register:

```bash
<host-b-cli> mcp add ncp-echo-pipeline -- \
  /abs/path/to/target/release/ncp-mcp-server \
  --graph /abs/path/to/examples/graphs/echo-pipeline/graph.yaml \
  --brick-dir /abs/path/to/examples/bricks \
  --trace-dir /abs/path/to/trace-dir
```

Verify + exercise all three levels:

```bash
<host-b-cli> exec --json --skip-git-repo-check \
  'Call MCP tool org.ncp-examples.echo-pipeline with arguments
  {"hello":"world"}. Report result_type.'
```

Expected: the JSONL event stream contains an item of type
`mcp_tool_call` with `server: "ncp-echo-pipeline"`,
`tool: "org.ncp-examples.echo-pipeline"`, `status: "completed"`, and
a `result` carrying `structured_content.result_type: "Success"`. A
new `<trace_id>.jsonl` file appears under the configured `--trace-dir`
containing a `runtime_info` record and one `invoke` record.

Clean up:

```bash
<host-b-cli> mcp remove ncp-echo-pipeline
```

## Adding a new host to the matrix

1. Register `ncp-mcp-server` with the host following its stdio-MCP
   configuration pattern. Prefer the lead-qualification graph if the
   host supports a non-trivial tool-use demonstration; otherwise use
   the echo-pipeline fixture.
2. Verify Level 1 (Connection) via the host's health/status output or
   log file.
3. Verify Level 2 (Discovery) via a `tools/list` round-trip or
   equivalent host tool-discovery output. Capability advertisement
   alone is insufficient.
4. Verify Level 3 (Execution) via a tool invocation that produces both
   a trace file AND surfaces `result_type` or `structuredContent` to
   the caller.
5. Note the host category (CLI / desktop / IDE / library), OS, and
   version string.
6. Append a row to the matrix with generic naming (CLI host C,
   desktop host A, etc.) following the §17 convention.
7. If a protocol-level quirk is observed, add it as a numbered entry
   under "Observed quirks" with the exact behavior and whether the
   server needs a change.

## See also

- [`docs/MCP_ADAPTER.md`](MCP_ADAPTER.md): the full binding contract
  (§17 covers the generic-terminology rule, §6 the error truth table)
- [`examples/mcp/echo-pipeline-mcp-smoke.md`](../examples/mcp/echo-pipeline-mcp-smoke.md): manual smoke-test dialog
- [`examples/mcp/desktop-host-config.example.json`](../examples/mcp/desktop-host-config.example.json): generic host config template
- [`examples/mcp/ci_smoke.py`](../examples/mcp/ci_smoke.py): automated validation using the official MCP Python SDK (CI gate)
- [issue #28](https://github.com/madeinplutofabio/neural-computation-protocol/issues/28)
