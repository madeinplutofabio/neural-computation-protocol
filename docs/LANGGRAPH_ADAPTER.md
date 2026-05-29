<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# NCP LangGraph Adapter (`ncp-langgraph`) - Design

This document is the binding design contract for `ncp-langgraph`, the Python
package that lets [LangGraph](https://github.com/langchain-ai/langgraph)
workflows invoke NCP graphs. It is the Phase 3A.3 analog of
[`docs/MCP_ADAPTER.md`](MCP_ADAPTER.md) (which froze the MCP adapter design
before PR B of Phase 3A.2 started).

Every architectural decision listed below was locked before any Python
code in `python/ncp-langgraph/` was authored. The remaining PRs (B
through G of Phase 3A.3) implement these decisions verbatim; deviations
require a follow-up issue and an updated revision of this doc, not an
inline reinterpretation during implementation.

Closes [#36](https://github.com/madeinplutofabio/neural-computation-protocol/issues/36).

---

## 1. Scope

`ncp-langgraph v0.1.0` is a thin Python package that wraps the existing
`ncp-mcp-server` binary so a LangGraph `StateGraph` can call an NCP graph
as a single node. It does NOT provide a general-purpose MCP client or
server library. It implements only the minimal stdio JSON-RPC sequence
needed to invoke `ncp-mcp-server` as a LangGraph node. It does NOT bind
to `ncp-runtime` directly, and does NOT expose any NCP API surface
beyond the LangGraph-node shape.

**In scope for v0.1.0:**

- Subprocess invocation strategy: spawn `ncp-mcp-server`, perform the
  JSON-RPC stdio dialog (`initialize` -> `notifications/initialized` ->
  `tools/list` -> `tools/call`), close stdin gracefully, assert clean
  exit.
- Synchronous Python API only.
- One subprocess spawned per `call_ncp_graph` invocation. No persistent
  pool.
- `NCPNode` class with a `from_subprocess(...)` classmethod factory.
  Instances are callable so they work directly as LangGraph nodes via
  `builder.add_node(name, instance)`.
- Lower-level `call_ncp_graph(...) -> RunnerResult` function for
  callers that want NCP from Python but do NOT need LangGraph state
  semantics.
- Typed exceptions for the four observable failure modes.

**Out of scope for v0.1.0** (see §15 for the full deferred list):

- PyO3 or any direct FFI binding to `ncp-runtime`.
- Streamable HTTP transport for `ncp-mcp-server` (separately tracked).
- Async `NCPAsyncNode` sibling class.
- Persistent subprocess pool, warm-cached runner, or long-lived
  service mode.
- Multi-graph `NCPNode` (one instance exposing several graphs through
  the same subprocess).
- Decorator-style API (`@ncp_node(...)`).
- Graph-level input schemas (the MCP `inputSchema` stays
  `{"type":"object"}` until the runtime supports declared schemas).

---

## 2. Mapping: one NCP graph = one `NCPNode`

Each `NCPNode` instance wraps exactly one NCP graph. Workflows that need
multiple graphs construct multiple `NCPNode` instances and register each
one independently under its own LangGraph node name.

```python
from ncp_langgraph import NCPNode

qualify_lead = NCPNode.from_subprocess(
    graph="/abs/path/to/lead-qualification.yaml",
    brick_dir="/abs/path/to/bricks",
    trace_dir="/tmp/ncp-traces",
    output_key="qualification",
)

triage_ticket = NCPNode.from_subprocess(
    graph="/abs/path/to/support-triage.yaml",
    brick_dir="/abs/path/to/bricks",
    output_key="triage",
)

# Both nodes register independently in the same StateGraph:
builder.add_node("qualify_lead", qualify_lead)
builder.add_node("triage_ticket", triage_ticket)
```

Each instance owns its own subprocess invocations: there is no shared
process state between `qualify_lead` and `triage_ticket`, and there is
no global registry. The unit of isolation is the `NCPNode` instance.

This mirrors Phase 3A.2's "one graph = one MCP tool" mapping
([`MCP_ADAPTER.md`](MCP_ADAPTER.md) §2). `ncp-langgraph` simply
projects that mapping into the LangGraph node model.

---

## 3. Public Python API

### 3.1 `NCPNode.from_subprocess(...)`

The only supported way to construct an `NCPNode` in v0.1.0. The factory
signature is locked verbatim:

```python
class NCPNode:
    @classmethod
    def from_subprocess(
        cls,
        graph: str | Path,
        brick_dir: str | Path,
        trace_dir: str | Path | None = None,
        binary: str | Path = "ncp-mcp-server",
        tool_name: str | None = None,
        input_key: str | None = None,
        output_key: str = "ncp_output",
        trace_key: str = "ncp_trace",
        timeout: float = 30.0,
    ) -> "NCPNode":
        ...
```

**Parameters:**

| Name | Type | Default | Purpose |
|---|---|---|---|
| `graph` | `str \| Path` | required | Absolute path to the NCP graph manifest (`graph.yaml`). |
| `brick_dir` | `str \| Path` | required | Absolute path to the directory containing brick subdirectories. |
| `trace_dir` | `str \| Path \| None` | `None` | If set, each call writes a per-call JSONL trace under this directory. `None` means traces are dropped (mirrors `ncp-mcp-server` `--trace-dir` semantics). |
| `binary` | `str \| Path` | `"ncp-mcp-server"` | Name or path of the `ncp-mcp-server` executable. Default resolves through `PATH`; pass an absolute path to bypass `PATH` resolution. |
| `tool_name` | `str \| None` | `None` | MCP tool name to invoke. Auto-derived if the underlying server exposes exactly one tool. Required if the server exposes multiple tools (raises `NCPAmbiguousToolError` otherwise). |
| `input_key` | `str \| None` | `None` | If set, only `state[input_key]` is passed as MCP `tools/call` `arguments`. If `None`, the whole LangGraph state dict is passed verbatim. |
| `output_key` | `str` | `"ncp_output"` | Key under which `structuredContent.output_json` is returned in the state-update dict. |
| `trace_key` | `str` | `"ncp_trace"` | Key under which the trace-metadata dict is returned in the state-update dict. |
| `timeout` | `float` | `30.0` | Wall-clock timeout in seconds for the full invocation (spawn + dialog + close + wait-for-exit). Raises `NCPTimeoutError` if exceeded. |

### 3.2 `NCPNode.__call__(state)`

```python
def __call__(self, state: dict) -> dict:
    ...
```

Returns a PARTIAL state-update dict (see §4 for full semantics). The
LangGraph runtime applies this partial update according to the
`StateGraph` state schema and reducers. **`__call__` does NOT mutate
the input `state`.**

Because instances are callable, they register as LangGraph nodes
directly:

```python
builder.add_node("ncp_step", my_node)
```

This is the canonical LangGraph callable-node integration pattern.

### 3.3 Low-level `call_ncp_graph(...)` function

For callers that need NCP from Python but do NOT use LangGraph:

```python
def call_ncp_graph(
    graph: str | Path,
    brick_dir: str | Path,
    arguments: dict[str, Any],
    *,
    trace_dir: str | Path | None = None,
    binary: str | Path = "ncp-mcp-server",
    tool_name: str | None = None,
    timeout: float = 30.0,
) -> RunnerResult:
    ...
```

Returns the `RunnerResult` dataclass directly (see §7). Same subprocess
machinery as `NCPNode` but without LangGraph state semantics.

### 3.4 No decorator in v0.1.0

`@ncp_node(...)` would create ambiguous semantics (does the decorated
function still run? is it pre/post-processing?). Explicitly excluded
from v0.1.0. The class + factory pattern is the only supported surface.

---

## 4. State input/output semantics

### 4.1 The contract

```python
update = node(state)
```

`update` is a NEW dict; `state` is unchanged. LangGraph applies this
partial update according to the `StateGraph` state schema and reducers.
The shape of `update` is:

```python
{
    node.output_key: structured_content["output_json"],
    node.trace_key: {
        "result_type": "Success" | "LowConfidence" | "Failure",
        "trace_id": "<uuid>",
        "trace_path": "<path>" | None,
    },
}
```

`output_key` defaults to `"ncp_output"`; `trace_key` defaults to
`"ncp_trace"`. Both are overridable in `from_subprocess(...)`.

### 4.2 What gets sent to `tools/call`

By default (`input_key=None`), the entire `state` dict is passed to the
MCP `tools/call` `arguments` field. This is the "whole-state pass-through"
mode: the graph receives the full LangGraph state as its root input and
decides which fields it cares about.

If `input_key` is set, only `state[input_key]` is passed. This is the
"narrow-input" mode: useful when the graph expects a tightly-shaped
input that doesn't match the LangGraph state schema.

### 4.3 Worked example

```python
# Setup
qualify_lead = NCPNode.from_subprocess(
    graph="/abs/path/to/lead-qualification.yaml",
    brick_dir="/abs/path/to/bricks",
    trace_dir="/tmp/ncp-traces",
    output_key="qualification",
)

# Input state from a previous LangGraph node:
state = {
    "company_url": "https://example.com",
    "target_icp": "B2B SaaS selling to creators",
}

# Invoke the node:
update = qualify_lead(state)

# `update` is a NEW dict (state is unchanged):
# {
#     "qualification": {
#         "icp_match": True,
#         "score": 82,
#         "reasons": ["Sells to creators", "Has subscription monetization"],
#         "next_action": "Send founder outreach",
#     },
#     "ncp_trace": {
#         "result_type": "Success",
#         "trace_id": "356b26f8-f9e7-4166-bd22-0f3eb8c3fc05",
#         "trace_path": "/tmp/ncp-traces/356b26f8-...jsonl",
#     },
# }

# LangGraph applies this partial update according to the StateGraph
# state schema and reducers; downstream nodes see (for a typical
# default-replace reducer):
# state["qualification"]["score"] == 82
# state["ncp_trace"]["result_type"] == "Success"
```

### 4.4 Immutability guarantee

`NCPNode.__call__` MUST NOT mutate `state`. This is verified by a unit
test in PR D (`test_node.py::test_state_immutability`): after the call,
`state` is equal to a deep copy captured before invocation.

The reason for the strict immutability is that LangGraph permits
sharing the same state dict across multiple node invocations and
across compile/invoke boundaries; mutation would create non-local
data-flow bugs that are hard to diagnose.

---

## 5. Invocation lifecycle

Each `call_ncp_graph` (and therefore each `NCPNode.__call__`) performs
this exact sequence:

1. Resolve the binary path (apply `.exe` suffix on Windows).
2. Build the `ncp-mcp-server` argv: `--graph`, `--brick-dir`, and
   `--trace-dir` (if set).
3. Spawn the subprocess with `stdin=PIPE`, `stdout=PIPE`,
   `stderr=<file>` (file-redirected, NOT `PIPE`, to avoid drain-deadlock
   per the lesson from `examples/mcp/ci_smoke.py`).
4. Start a global `threading.Timer` for `timeout` seconds. On fire,
   kill the subprocess; subsequent I/O raises and the caller sees
   `NCPTimeoutError`.
5. Send the four JSON-RPC frames:
   - `initialize` (request)
   - `notifications/initialized` (notification, no response expected)
   - `tools/list` (request)
   - `tools/call` (request)
6. Parse each response; assert every stdout line is valid JSON with
   `jsonrpc == "2.0"` (mirrors the `ci_smoke.py` discipline).
7. Close stdin. This signals EOF to the server, which enters rmcp's
   graceful-drain path (see [`MCP_ADAPTER.md`](MCP_ADAPTER.md) §11 for
   the Tokio `.enable_time()` requirement).
8. Wait up to 10 seconds for the subprocess to exit. Assert
   `returncode == 0`. Any non-zero exit means the server panicked
   during graceful shutdown, which raises `NCPSubprocessError`.
9. Cancel the timer, build the `RunnerResult`, return.

### 5.1 One subprocess per call (v0.1.0 lock)

Each call to `call_ncp_graph` spawns a fresh `ncp-mcp-server` process,
performs the dialog, and tears it down. There is no persistent process,
no warm-runner pool, no shared state across calls.

Subprocess startup adds measurable per-call overhead, so v0.1.0 is
intended for workflow nodes where deterministic graph execution is not
in a hot inner loop. Persistent-subprocess mode is deferred to v0.2.0+.
PyO3 direct binding is the long-term answer for hot loops.

### 5.2 Stderr discipline

The subprocess's stderr is redirected to a temp file (per
`ci_smoke.py`'s pattern). Stderr is NOT plumbed to a pipe because an
undrained pipe can fill its OS buffer (64KB on Linux) and deadlock the
subprocess. On any failure path, the wrapper reads the temp file's tail
and includes it in the exception message for diagnostics. On success,
the temp file is deleted.

---

## 6. Error model

`ncp-langgraph` raises four typed exceptions, all inheriting from a
base `NCPError` for callers that want to catch any failure with a
single `except` clause.

### 6.1 Exception hierarchy

```
NCPError (base)
├── NCPSubprocessError       # subprocess-level or JSON-RPC-level failures
├── NCPInvocationError       # graph executed but returned isError=true
├── NCPTimeoutError          # overall timeout exceeded
└── NCPAmbiguousToolError    # multi-tool server without explicit tool_name
```

### 6.2 Raise conditions

| Exception | When raised |
|---|---|
| `NCPSubprocessError` | Subprocess spawn failed; subprocess exited non-zero (including graceful-drain panic); JSON-RPC error response from the server (parse error, method not found, etc.); subprocess closed stdout unexpectedly. |
| `NCPInvocationError` | The MCP `tools/call` returned a valid JSON-RPC response, but `result.isError == true`. The exception carries the structured content so callers can inspect the trace. |
| `NCPTimeoutError` | The global `timeout` parameter was exceeded. The subprocess is killed before the exception propagates. |
| `NCPAmbiguousToolError` | The server's `tools/list` returned more than one tool, and the caller did not pass `tool_name` to `from_subprocess`. Raised at first invocation, NOT at construction time (so single-tool servers don't pay the cost). With the v0.1.0 one-graph factory, ambiguity should not occur in normal use; this exception is retained as a defensive guard against unexpected `tools/list` responses and future multi-graph extensions. |

### 6.3 `NCPInvocationError` carries trace metadata

Graph failures are still useful: the trace file exists, the per-terminal
errors are in `structuredContent.terminal_results[i].error`, and the
caller usually wants to inspect them rather than retry blindly.
`NCPInvocationError` exposes four attributes:

```python
class NCPInvocationError(NCPError):
    structured_content: dict[str, Any]   # full §5 response shape
    result_type: str                      # "Failure" | "LowConfidence"
    trace_id: str
    trace_path: str | None                # None if --trace-dir was not set
```

Typical caller code:

```python
try:
    update = node(state)
except NCPInvocationError as e:
    log.warning(
        "graph %s returned %s; trace at %s",
        node.graph, e.result_type, e.trace_path,
    )
    # Inspect e.structured_content["terminal_results"] for per-terminal
    # error objects, then decide whether to retry, fall back, or escalate.
```

### 6.4 JSON-RPC errors vs MCP-level errors

The distinction matters for retry logic:

- **JSON-RPC error response** -> `NCPSubprocessError`. The server failed
  to process the request at the protocol layer (malformed input,
  missing method, internal error). Retrying the same call is unlikely
  to succeed.
- **MCP `result.isError == true`** -> `NCPInvocationError`. The graph
  ran, produced a result, and that result was a Failure or
  LowConfidence rollup per [`MCP_ADAPTER.md`](MCP_ADAPTER.md) §5/§6.
  The trace explains why. Retrying may make sense depending on the
  error class.

---

## 7. `RunnerResult` dataclass

```python
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class RunnerResult:
    output_json: Any
    structured_content: dict[str, Any]
    trace: dict[str, Any]   # {"result_type", "trace_id", "trace_path"}
```

`call_ncp_graph(...)` returns this dataclass directly.
`NCPNode.__call__` internally maps it to the LangGraph state-update
dict per §4.

`frozen=True` prevents reassignment of the dataclass fields. The nested
JSON values remain normal Python objects; callers that need deep
immutability should copy or freeze them at the boundary. `output_json`
is `Any` because graphs return arbitrary JSON-serializable values;
type-narrowing happens at the caller level (or via the future
graph-level input/output schema work tracked separately).

---

## 8. v0 limitations and non-goals

State honestly. The following limitations are intrinsic to v0.1.0 and
should be documented in the `python/ncp-langgraph/README.md` quick-start:

- Subprocess spawn cost per invocation: measurable per-call overhead;
  not suitable for hot inner loops. (Benchmarks deferred to a future
  release with real adopter workloads.)
- One subprocess per call (no warm pool in v0.1.0).
- Stdio transport only (no Streamable HTTP).
- Synchronous API only (no native async support).
- No PyO3 or direct runtime binding.
- Graphs see `{"type":"object"}` as their MCP input schema regardless
  of internal manifest contents (depends on the upstream MCP adapter
  contract).
- Requires `ncp-mcp-server` installed on the host (either on `PATH`
  or via the `binary=` argument). The Python package does NOT install
  the Rust binary as a side-effect; that is a separate
  `cargo install ncp-mcp-server --locked` step (per §10).

### 8.1 Explicit non-goal

**`ncp-langgraph` does NOT expose the NCP runtime, graph parser,
validator, or brick APIs to Python. It ONLY wraps the already-published
`ncp-mcp-server` process for LangGraph node usage. A general Python
SDK is Phase 3A.4 work, not Phase 3A.3.**

This non-goal exists to prevent scope creep during PR review. The
LangGraph wrapper is intentionally narrow: it is one Python class plus
one helper function, neither of which should accumulate features that
belong in a hypothetical future `ncp-python-sdk` package.

---

## 9. Workspace structure

```
neural-computation-protocol/
├── crates/                 # Rust crates (ncp-mcp-server lives here)
├── runtime/                # Reference runtime (Rust)
├── bricks/                 # Reference brick implementations (Rust -> WASM)
├── examples/               # Brick + graph manifests, fixtures, demo graphs
│   ├── mcp/                # MCP host config + smoke recipes + ci_smoke.py
│   └── langgraph/          # Added in PR E: LangGraph workflow example
├── python/                 # Added in this phase: Python packages
│   └── ncp-langgraph/      # The package itself (this phase)
├── spec/                   # Protocol spec
├── tools/                  # Validator CLI
├── conformance/            # Test vectors
└── docs/                   # Documentation (this file lives here)
```

### 9.1 Why `python/` and not `crates/ncp-langgraph/`

`crates/` is the home for Rust crates; the workspace `Cargo.toml`
treats it as such. `ncp-langgraph` is a Python package, not a Rust
crate, so it must live outside the Rust workspace tree. A new
top-level `python/` directory provides a clear home for current and
future Python packages without entangling them with Cargo's workspace
discovery.

### 9.2 Future Python packages

If future phases add other Python packages (e.g., a general
`ncp-python-sdk`, brick-author tooling, evaluation harnesses), they
land as siblings under `python/`:

```
python/
├── ncp-langgraph/          # this phase
├── ncp-python-sdk/         # hypothetical Phase 3A.4
└── ncp-tools/              # hypothetical future
```

Each `python/*` package owns its own `pyproject.toml`, `CHANGELOG.md`,
and PyPI release ceremony.

---

## 10. `ncp-mcp-server` dependency

`ncp-langgraph` requires the `ncp-mcp-server` binary at runtime. This
dependency is documented but NOT enforced through pip:

- `pyproject.toml` does NOT list `ncp-mcp-server` as a Python
  dependency (pip cannot install a Rust binary).
- `python/ncp-langgraph/README.md` declares the dependency in the
  Install section: "Requires `ncp-mcp-server` from crates.io: run
  `cargo install ncp-mcp-server --locked` first, then
  `pip install ncp-langgraph`."
- `NCPNode.from_subprocess(binary=...)` accepts an explicit path,
  letting callers point at a non-PATH location.
- The package raises `NCPSubprocessError` with a clear message at
  first invocation if the binary cannot be found or spawned.

### 10.1 Version compatibility

`ncp-langgraph v0.1.0` targets `ncp-mcp-server v0.1.x`. The MCP wire
contract ([`MCP_ADAPTER.md`](MCP_ADAPTER.md) §5) is stable across patch
releases of the adapter; `ncp-langgraph` does NOT pin to a specific
`ncp-mcp-server` patch version because the wire shape is the actual
contract, not the binary version.

If a future `ncp-mcp-server v0.2.0` changes the wire shape (unlikely
for stdio; possible if Streamable HTTP becomes default), `ncp-langgraph`
will ship a corresponding v0.2.0 of its own and document the
compatibility matrix.

---

## 11. Async support deferral

`v0.1.0` is synchronous only. `NCPNode.__call__` is a regular `def`,
not `async def`. The lower-level `call_ncp_graph` is likewise sync.

### 11.1 What this means for async LangGraph workflows

The package makes NO promise about behavior inside async LangGraph
graphs beyond what is testable in PR D's `test_node.py`. If the README
or other adopter docs claim that sync nodes work inside async graphs,
that claim MUST be backed by a corresponding test in `test_node.py`;
otherwise the claim is softened to: "v0.1.0 exposes a synchronous
LangGraph node. Native async support is deferred."

### 11.2 Future async path

`NCPAsyncNode` is the planned v0.2.0+ addition. It will share the
subprocess-runner implementation but expose an `async def __call__`.
The factory will be `NCPAsyncNode.from_subprocess(...)` with the same
signature as `NCPNode.from_subprocess(...)`.

---

## 12. Versioning policy

`ncp-langgraph` starts at v0.1.0. Its version is **decoupled from**
`ncp-runtime` and `ncp-mcp-server`; bumps reflect adapter API or
behavior changes only.

Each adapter has its own release ceremony. The phase-3A.2 lesson is
that mixing adapter versions in a single linear history obscures
which release introduced a given behavior change. `ncp-langgraph`
follows the same crate-local CHANGELOG pattern.

---

## 13. CHANGELOG strategy

`python/ncp-langgraph/CHANGELOG.md` is the package-local changelog.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [SemVer](https://semver.org/spec/v2.0.0.html).

The root `CHANGELOG.md` is unchanged. It continues to cover
`ncp-runtime`, the NCP specification, and project-wide changes only.

---

## 14. Tag pattern (LOCKED)

| Package | Tag pattern | Workflows that fire on push |
|---|---|---|
| `ncp-runtime` (Rust) | `v0.3.6`, `v0.3.7-rc.1`, etc. | `release.yml` + `docker.yml` (correct: runtime release) |
| `ncp-mcp-server` (Rust) | `ncp-mcp-server-v0.1.0`, `ncp-mcp-server-v0.1.0-rc.1`, etc. | None (correct: crates.io-first) |
| `ncp-langgraph` (Python) | `ncp-langgraph-v0.1.0`, `ncp-langgraph-v0.1.0-rc.1`, etc. | None (correct: PyPI-first) |

**Adapter tags MUST NOT start with `v`.** The existing `release.yml`
and `docker.yml` fire on `push: tags: ['v*']` and would mis-handle an
adapter tag, publishing wrong artifacts under it. The crate-name-
prefixed pattern is the standard multi-crate workspace convention
(see how `tokio-1.x.y`, `hyper-vX.Y.Z` etc. coexist in their
workspaces).

This rule is enforced by `docs/PUBLISHING.md` "Adapter publish"
sections, which require a `gh run list --limit 10` check after every
adapter tag push to confirm no runtime workflows fired.

---

## 15. Examples

Adopter-facing examples live in
[`examples/langgraph/`](https://github.com/madeinplutofabio/neural-computation-protocol/tree/main/examples/langgraph).

The v0.1.0 example is a lead-qualification workflow shape: a LangGraph
`StateGraph` with one `NCPNode`. It is currently stubbed on the bundled
`echo-pipeline` NCP graph until issue
[#29](https://github.com/madeinplutofabio/neural-computation-protocol/issues/29)
ships the real `examples/graphs/lead-qualification/` graph.

Substitution is documented inline in the example script (`TODO(#29)`
comment in the module docstring) and in
[`examples/langgraph/README.md`](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/examples/langgraph/README.md).

Run end-to-end from a workspace clone:

```bash
cargo install ncp-mcp-server --version 0.1.0 --locked
python -m pip install ncp-langgraph
python -m pip install -r examples/langgraph/requirements.txt
python examples/langgraph/lead_qualification_agent.py
```

Expected output: the final LangGraph state dict with `company_url`, `target_icp`, `qualification`, and `ncp_trace`.

## 16. Out of scope (deferred)

Tracked items that v0.1.0 explicitly does NOT ship:

| Item | Deferred to |
|---|---|
| Native async `NCPAsyncNode` | v0.2.0+ |
| HTTP runner (`NCPNode.from_http`) | After `ncp-mcp-server` ships Streamable HTTP transport (issue [#34](https://github.com/madeinplutofabio/neural-computation-protocol/issues/34)) |
| PyO3 / direct runtime runner (`NCPNode.from_runtime`) | Separate phase; requires `maturin` + manylinux wheel infrastructure |
| Persistent / warm-cached subprocess pool | v0.2.0+ perf optimization |
| Multi-graph `NCPNode` (one instance, multiple graphs) | v0.2.0+ |
| Graph-level input schemas | Depends on issue [#31](https://github.com/madeinplutofabio/neural-computation-protocol/issues/31) (graph manifest `input_schema:` field) |
| `@ncp_node(...)` decorator API | Locked out of v0 per scope discipline (§3.4) |
| Authoring the `lead-qualification` graph itself | Out of scope; tracked separately as issue [#29](https://github.com/madeinplutofabio/neural-computation-protocol/issues/29). PR E uses the existing `echo-pipeline` graph as a stub. |
| All Phase 3A.4 SDKs (`ncp-python-sdk` proper, TypeScript SDK, brick-author tooling) | Phase 3A.4 |

---

## References

- [`docs/MCP_ADAPTER.md`](MCP_ADAPTER.md) for the Phase 3A.2 binding contract; this doc mirrors its structure.
- [`docs/PUBLISHING.md`](PUBLISHING.md) "Adapter publish (`ncp-mcp-server`)" for the template the upcoming "Python adapter publish (`ncp-langgraph`)" section will mirror in PR F.
- [`docs/ROADMAP.md`](ROADMAP.md) §3A.3 for the Phase 3A.3 roadmap entry.
- [`docs/BRANCH_PROTECTION.md`](BRANCH_PROTECTION.md) for the procedure to add `langgraph-test` to the required-check set after PR G.
- [`examples/mcp/ci_smoke.py`](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/examples/mcp/ci_smoke.py) for the direct reference for PR C's subprocess-runner implementation (stderr file-redirect, threading.Timer timeout, graceful-shutdown gate, Windows `.exe` handling).
- [LangGraph](https://github.com/langchain-ai/langgraph) for the orchestration library this adapter integrates with.
- [Model Context Protocol](https://modelcontextprotocol.io) for the underlying protocol used by `ncp-mcp-server`.
