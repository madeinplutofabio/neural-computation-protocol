<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# Python SDK Design Contract (`ncp-runtime`)

**Status.** Frozen before implementation (PR 3A.4-1). Implementation PRs
(3A.4-2 package skeleton, 3A.4-3 tests + measured-comparison example,
3A.4-4 bump + platform wheels + publish) treat this document as the
locked contract. Changing a contract documented here requires a
design-doc update PR that merges before any dependent implementation PR.

**At a glance.**
- PyPI distribution: `ncp-runtime`
- Python import module: `ncp_runtime`
- Target: Python 3.10+ on Linux, macOS, Windows
- Depends on: the Rust `ncp` binary (produced by the `ncp-runtime` crate),
  currently pinned to exactly v0.3.6
- License: Apache-2.0
- SPDX + copyright header on every tracked file:
  `# SPDX-License-Identifier: Apache-2.0` +
  `# Copyright 2026 Fabio Marcello Salvadori`

> This Python package is the SDK for the Neural Computation Protocol
> (NCP) runtime. It wraps and invokes the `ncp` binary (produced by the
> Rust `ncp-runtime` crate). The Python distribution is NOT the Rust
> executable; it is a Python package that spawns and talks to that
> executable, with the executable bundled inside platform-specific
> wheels where feasible.

---

## 1. Scope and audience

`ncp-runtime` is the general-purpose Python entry point for running NCP
graphs. It is for Python developers who want to execute an NCP graph
and read its structured result, without:

- learning Rust or the brick ABI,
- adopting LangGraph (`ncp-langgraph` serves that path),
- standing up an MCP host (`ncp-mcp-server` serves that path),
- or manually orchestrating subprocess invocations and trace files.

**Non-audience.** This SDK is not for authoring bricks, modeling graph
topologies, embedding Wasmtime, or re-implementing graph execution in
Python. Those remain the domain of the Rust crates.

The SDK's deliberate v0.1.0 surface is the smallest API that lets an
adopter answer three questions:

1. Did the graph run?
2. What did its terminals produce (Success / LowConfidence / Failure,
   plus per-terminal output / error)?
3. What steps did it take? (via a `Trace` iterator over the JSONL file
   the runtime wrote.)

Nothing more ships in v0.1.0.

---

## 2. Relationship to `ncp-langgraph`, `ncp-mcp-server`, and the runtime CLI

NCP exposes four distinct execution paths today, each with its own
transport and target audience:

| Path | Transport | Target audience | Package / binary |
|---|---|---|---|
| MCP adapter | stdio JSON-RPC (MCP) | Hosts that speak MCP (desktop AI apps, CLIs) | `ncp-mcp-server` |
| LangGraph adapter | MCP under the hood | LangGraph builders | `ncp-langgraph` (PyPI) |
| **Python SDK (this doc)** | **direct `ncp run` CLI** | **Python developers (no LangGraph, no MCP host)** | **`ncp-runtime` (PyPI, this package)** |
| TypeScript SDK (future) | direct `ncp run` CLI | Node/TS developers | `@ncp/runtime` (future planning cycle 3A.4-TS) |

**One execution engine, multiple integrations.** All four paths above
share the same `ncp-runtime` Rust engine. The Python SDK uses the
runtime's CLI contract directly (same as the future TypeScript SDK will).
MCP semantics belong to the MCP adapter; LangGraph semantics belong to
`ncp-langgraph`. The Python SDK does NOT depend on either of those, does
NOT embed Wasmtime, and does NOT re-implement graph execution.

**No consolidation in this phase.** `ncp-langgraph` v0.1.0 already
contains a complete MCP dialog driver (`call_ncp_graph`). It stays
unchanged. A future backward-compatible refactor to delegate to this
SDK is out of 3A.4 scope.

---

## 3. Public Python API (v0.1.0)

Full public surface of `from ncp_runtime import ...`. Everything not
listed here is private. Private names begin with `_`.

Module-level constants:

```python
__version__: str                              # SDK version, from _version.py
SUPPORTED_RUNTIME_VERSIONS: tuple[str, ...]   # v0.1.0: ("0.3.6",)
```

### 3.1 `NCP` entry point

Programmatic entry point for executing NCP graphs. One instance per SDK
consumer; one subprocess per `.run()` call in v0.1.0.

```python
class NCP:
    def __init__(
        self,
        *,
        binary: str | Path | None = None,
        timeout: float = 60.0,
    ) -> None: ...

    def run(
        self,
        graph: str | Path,
        brick_dir: str | Path,
        inputs: dict[str, Any],
        *,
        brick_map: str | Path | None = None,
        max_steps: int | None = None,
    ) -> "Result": ...
```

**Binary resolution precedence** (first non-None wins):

1. The `binary` kwarg (user override).
2. The platform binary bundled inside the installed wheel, discovered at
   `importlib.resources.files("ncp_runtime") / "_bundled" / "ncp[.exe]"`.
3. `ncp` on PATH, via `shutil.which("ncp")`.
4. If none resolves, raise `NCPBinaryNotFoundError` with an actionable
   message naming all three options and, when the installation is the
   `py3-none-any` fallback (no bundled binary), the remediation path
   (see §10).

**`binary` kwarg.** When set, must point to an existing file; otherwise
raises `NCPBinaryNotFoundError` with the explicit-path context.

**`timeout`.** Wall-clock budget for the whole subprocess call. On
expiry: the process tree is terminated (§4.4), both temp files are
deleted, and `NCPTimeoutError` is raised. Default 60s.

**`inputs`.** Python dict serialized by `json.dumps` and written to a
temp input file. Non-serializable input raises `TypeError` from
`json.dumps` BEFORE any subprocess is spawned (no temp file created).

**Constructor signature is deliberately open** to a future
`persistent=True` or similar refinement without a breaking change. See
§10 for the API-shape sketch.

### 3.2 `Result` and `Terminal`

Return value from `.run()`. Immutable terminal rollup; owns the trace
file on disk.

```python
class Result:
    terminals: tuple[Terminal, ...]
    result_type: str                       # "Success" | "LowConfidence" | "Failure"
    trace_path: Path                       # SDK-owned temp file

    def trace(self) -> Iterator["TraceRecord"]: ...
    def cleanup(self) -> None: ...         # idempotent
    def __enter__(self) -> "Result": ...
    def __exit__(self, *exc_info: Any) -> None: ...


@dataclass(frozen=True)
class Terminal:
    node_id: str
    brick_id: str
    step: int
    result_type: str                       # "Success" | "LowConfidence" | "Failure"
    output: Any | None                     # Present iff CLI emitted an `output` field
    error: "TerminalError | None"          # Present iff CLI emitted an `error` field


@dataclass(frozen=True)
class TerminalError:
    error_class: str
    message: str
```

**Common usage.** No cleanup understanding required:

```python
result = ncp.run(graph, brick_dir, inputs)
if result.result_type == "Success":
    print(result.terminals[0].output)
```

**Explicit cleanup** via context manager or `.cleanup()` for
long-running processes, test suites, or strict resource accounting:

```python
with ncp.run(graph, brick_dir, inputs) as result:
    ...
```

**Why no `.output` convenience property.** In multi-terminal graphs,
picking "the output" is ambiguous; forcing a single value would hide
that ambiguity. Callers interrogate `.terminals` directly.

**`output` and `error` field semantics.** Parsed by their PRESENCE in
CLI output, NOT inferred from `result_type`. LowConfidence terminals
can legitimately carry both an output AND an error per the Rust
runtime's `BrickResult` type. See §4.1 for the CLI-to-SDK conversion.

**Cleanup lifecycle rules:**

- `weakref.finalize` is attached at construction to delete the trace
  file on GC as a safety net.
- After `.cleanup()` or `__exit__` runs, the finalizer MUST be detached;
  GC of a cleaned-up Result does not attempt a second unlink.
- `.cleanup()` is idempotent.
- `.cleanup()` is reliable even if a prior `.trace()` raised
  mid-iteration (the iterator closes its handle before raising; see §8).
- Trace iteration must complete before cleanup. See §8.
- Temp INPUT files are NEVER owned by `Result`. They are deleted during
  `.run()`'s `finally` block (§5 step 6), BEFORE `Result` is
  constructed.

### 3.3 `Trace` iterator and record variants

Typed records yielded by `Result.trace()`.

```python
@dataclass(frozen=True)
class RuntimeInfoRecord:
    trace_schema_version: str              # expected: "ncp-trace-0.1"
    runtime_version: str
    wasmtime_version: str
    timestamp: str                         # RFC3339


@dataclass(frozen=True)
class InvokeRecord:
    trace_schema_version: str
    timestamp: str
    trace_id: str
    session_id: str
    step: int
    graph_id: str
    graph_version: str
    brick_id: str
    brick_version: str
    bundle_digest: str                     # "sha256:<hex>"
    node_id: str
    trigger: dict[str, Any]
    input_hash: str                        # "sha256:<hex>"
    output_hash: str | None
    result_len: int | None
    result_type: str                       # "Success" | "LowConfidence" | "Failure"
    latency_ms: float
    error_class: str | None


@dataclass(frozen=True)
class UnknownTraceRecord:
    type: str
    raw: dict[str, Any]


TraceRecord = RuntimeInfoRecord | InvokeRecord | UnknownTraceRecord
```

Iterator semantics are specified in §8.

---

## 4. Backend contract: `ncp run --all-terminals`

The SDK spawns the resolved `ncp` binary with:

```
ncp run <graph.yaml>
  --input <sdk-temp-input.json>
  --brick-dir <brick_dir>
  --trace <sdk-temp-trace.jsonl>
  --all-terminals
  [--brick-map <brick_map>]
  [--max-steps <n>]
```

`--all-terminals` is required.

### 4.1 CLI-to-SDK conversion table

Schema sketch (NOT a literal JSON document; `|` denotes type union,
`<str>` / `<int>` / `<any>` are placeholders, `// optional` is a comment):

```
{
  "node_id":  "<str>",
  "brick_id": "<str>",
  "step":     <int>,
  "type":     "Success" | "LowConfidence" | "Failure",
  "output":   <any, optional>,
  "error":    { "error_class": "<str>", "message": "<str>" }   // optional
}
```

Field mapping:

| CLI field | SDK field | Notes |
|---|---|---|
| `node_id` (str) | `Terminal.node_id` | 1:1 |
| `brick_id` (str) | `Terminal.brick_id` | 1:1 |
| `step` (int) | `Terminal.step` | 1:1 |
| `type` (str) | `Terminal.result_type` | Field renamed; values preserved verbatim |
| `output` (any, optional) | `Terminal.output` | Present iff CLI emits it; `None` otherwise |
| `error` (object, optional) | `Terminal.error` | Present iff CLI emits it; wrapped as `TerminalError(error_class, message)` |

**Parse `output` and `error` by field presence, not by `result_type`.**
The MCP adapter's `structuredContent` wire schema is a DIFFERENT boundary
and not interchangeable with this CLI JSON schema.

**Strict value validation (defensive parsing, NOT a runtime handshake).**
Every element of the top-level array is validated BEFORE being
converted to a `Terminal`. A violation of any rule below is treated as
malformed stdout under §4.3 and raises `NCPSubprocessError`:

- `node_id`, `brick_id`, `step`, `type` MUST all be present.
- Field types: `node_id` str, `brick_id` str, `type` str, `step` int.
  `step` specifically rejects `bool` and `float`.
- **`type` is a closed enum:** exactly one of `"Success"`,
  `"LowConfidence"`, `"Failure"`. Unknown values are NEVER silently
  treated as any enum member.
- `output`, when present, passes through as-is.
- `error`, when present, MUST be an object with string `error_class`
  AND string `message`.

Adding a new `type` enum value is a coordinated SDK-plus-runtime release.

**Rollup rule for `Result.result_type`:**

```
any Failure            -> "Failure"
else any LowConfidence -> "LowConfidence"
else                   -> "Success"
```

Must match MCP adapter semantics
([`crates/ncp-mcp-server/src/server.rs:382-399`](../crates/ncp-mcp-server/src/server.rs)).

### 4.2 Runtime-SDK compatibility contract

Before the first `.run()`, the SDK executes `<resolved-binary> --version`
and parses the output. Format is clap-provided: `ncp <version>`.
Matched against `SUPPORTED_RUNTIME_VERSIONS`.

**v0.1.0 constant:** `SUPPORTED_RUNTIME_VERSIONS = ("0.3.6",)`.

**Policy.** Exact-pin tuple. A matching version string is necessary but
not sufficient; §4.1 strict validation catches an incompatible or
malformed binary even when `--version` matches, without requiring a
new runtime handshake protocol.

### 4.3 Nonzero-exit rule (SDK failure vs graph failure)

Strict by default. Three cases, resolved IN ORDER:

1. **Exit 0 AND stdout is a valid nonempty JSON array matching the §4.1
   element shape AND passing §4.1 strict validation:** success.
2. **Any non-zero exit code:** raise `NCPSubprocessError`. The
   allowlist for "nonzero exit corresponds to a valid graph outcome"
   is EMPTY per PR 1 verification.
3. **Exit 0 with malformed stdout, empty stdout, OR §4.1 strict
   validation failure:** raise `NCPSubprocessError`.

**Empirical PR 1 findings:**

| Scenario | Exit | Stdout | SDK outcome |
|---|---|---|---|
| Terminal Success (lead-qualification `qualified_3_of_3.json`) | 0 | Valid non-empty array | Return `Result(result_type="Success")` |
| Terminal Failure (lead-qualification `failure_missing_url.json`) | 0 | Valid non-empty array | Return `Result(result_type="Failure")` |
| Missing graph file | 1 | 0 bytes | `NCPSubprocessError` |
| Malformed YAML graph | 1 | 0 bytes | `NCPSubprocessError` |
| Missing input file | 1 | 0 bytes | `NCPSubprocessError` |
| Missing brick-dir | 1 | 0 bytes | `NCPSubprocessError` |

LowConfidence integration coverage follows when a graph that triggers
the variant becomes available; a fixture-based test covers it earlier.

### 4.4 Subprocess hardening (locked)

1. **Argument arrays only.** `subprocess.Popen([binary, "run", ...])`.
   Never `shell=True`.

2. **Explicit process-group management for timeout and crash cleanup.**
   `subprocess.run(timeout=...)` kills only the direct child. The SDK
   uses `subprocess.Popen` with per-platform setup and terminates the
   WHOLE tree on timeout:
   - **POSIX:** `start_new_session=True`; on timeout
     `os.killpg(os.getpgid(proc.pid), SIGTERM)`; brief wait; `SIGKILL`
     if still alive; `proc.wait()` to reap.
   - **Windows:** `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP`;
     on timeout terminate via job-object attached at spawn, OR
     descendant walk + `TerminateProcess` (via `psutil` if the dep is
     acceptable, else `taskkill /T /F`). PR 2 picks and documents the
     approach and covers it with the cleanup test (rule 6).
   - Both temp files unlinked in a `finally` block BEFORE
     `NCPTimeoutError` is raised.

3. **Controlled handling of stderr contents.** Raw stderr MAY contain
   user input, file contents, or data a brick's error path echoed. The
   SDK treats captured stderr as potentially sensitive.
   - Exception `__str__` and `.args` carry: category (`"nonzero_exit"`,
     `"malformed_stdout"`, `"strict_validation_failed"`,
     `"output_cap_exceeded"`), exit code (int or None), resolved binary
     path, and stderr LENGTH in bytes. No stderr contents. No input
     dict.
   - Full stderr lives on `NCPSubprocessError.stderr: bytes` so callers
     who deliberately want it can read it.
   - `NCP_RUNTIME_STDERR_IN_MESSAGE=1` (opt-in env var) flips the
     default to include the stderr tail in `__str__`/`.args` for
     debugging builds. Off in production. No Python-API flag.
   - SDK loggers NEVER emit `inputs`, raw stderr, or input/trace file
     CONTENTS. File paths are allowed.

4. **Paths with spaces and Unicode** must round-trip on all three OSes.
   Fixture directory in tests contains a space + non-ASCII character.

5. **Temp input files deleted promptly** (before `Result` returns, in a
   `finally` block). Trace temp files deleted by Result cleanup /
   context exit / weakref.finalize GC.

6. **Cross-platform cleanup tests.** PR 2 ships a test that spawns a
   simulated long-running subprocess (shim replacing `ncp` that spawns
   a sleep-forever descendant) with a short timeout, and asserts:
   `NCPTimeoutError` raised; BOTH direct child AND descendant gone;
   both temp files unlinked. Runs on ubuntu-24.04 and windows-2022.

7. **Execution errors stay distinguishable** from graph outcomes (§4.3).

8. **Concurrent draining with actively enforced output caps.** The SDK
   MUST drain stdout and stderr concurrently with the subprocess, each
   stream capped (stdout 16 MiB, stderr 1 MiB).
   - **Plain `Popen.communicate()` is INSUFFICIENT.** It drains both
     pipes but buffers the output in memory without bound. A
     misbehaving executable emitting > 16 MiB would still exhaust
     memory before `communicate()` returned.
   - **Required implementation:** a pair of concurrent reader threads
     (or a POSIX selector-based async reader), each reading one pipe
     into a bounded buffer AND actively enforcing the cap during
     execution:
     - Count bytes written into the per-stream buffer against the cap.
     - When the cap is reached: STOP appending to the buffer, but
       CONTINUE reading from the pipe into a bit-bucket so the child
       cannot block on a full pipe. Set an `overflowed` flag.
     - Signal the main thread when the flag is first set so it can
       initiate tree termination.
   - **Main-thread responsibilities:** `proc.wait(timeout=remaining)`
     while readers drain concurrently. On TimeoutExpired OR either
     reader's cap-exceeded signal: terminate the process tree (rule
     2); join the readers (they finish bit-bucket draining and close
     their pipe ends); unlink both temp files in a `finally` block;
     raise `NCPTimeoutError` or `NCPSubprocessError(category="output_cap_exceeded")`.
   - **Test:** a shim binary that spews > 16 MiB on stdout triggers
     the cap, exits the tree, and surfaces the correctly-categorized
     exception, with the SDK's additional memory growth attributable
     to captured output bounded by the cap relative to a pre-run
     baseline (not an absolute total-RSS bound, which Python's
     baseline overhead already exceeds).

---

## 5. Invocation lifecycle

One subprocess per `.run()` call in v0.1.0. Lifecycle:

```
NCP.run(graph, brick_dir, inputs)
    |
    +- 1. Resolve binary (precedence: user kwarg -> bundled -> PATH)
    |        -> NCPBinaryNotFoundError if none
    |        -> resolved path cached on self; subsequent calls reuse it
    +- 2. Run `<binary> --version`, parse, check SUPPORTED_RUNTIME_VERSIONS
    |        -> NCPRuntimeVersionError if unsupported
    |        -> cached on self; subsequent calls skip
    +- 3. json.dumps(inputs) -> write to tempfile.NamedTemporaryFile(delete=False)
    |        -> TypeError propagates to caller before any subprocess spawn
    +- 4. Allocate trace tempfile (delete=False, SDK-owned).
    |        From here through step 10, SDK retains sole ownership of
    |        the trace tempfile (see §5.1).
    +- 5. Spawn via subprocess.Popen with per-platform process-group
    |        setup (§4.4 rule 2). Start concurrent cap-enforcing reader
    |        threads/selectors per §4.4 rule 8 (plain communicate() is
    |        insufficient). Wait for exit via proc.wait(timeout=remaining).
    |        On TimeoutExpired: terminate the process tree; join
    |        readers; unlink both temp files; raise NCPTimeoutError.
    |        On either reader's cap-exceeded signal: terminate the
    |        process tree; join readers; unlink both temp files; raise
    |        NCPSubprocessError(category="output_cap_exceeded").
    +- 6. DELETE temp input file IMMEDIATELY (finally block;
    |        runs on both success and failure paths)
    +- 7. Apply §4.3 nonzero-exit rule. On failure: construct
    |        NCPSubprocessError per §4.4 rule 3 (category + exit code +
    |        binary path + stderr length in message; full stderr on
    |        the public `.stderr` attribute). Trace tempfile unlinked
    |        by the §5.1 ownership policy before raising.
    +- 8. Parse JSON array; apply §4.1 strict value validation; convert
    |        to tuple[Terminal, ...]
    +- 9. Compute rollup result_type via §4.1 rule
    +-10. Build Result(terminals=..., result_type=..., trace_path=<trace tempfile>).
          Attach weakref.finalize(result, _unlink_quiet, trace_path);
          store the finalize handle on Result. Ownership transfers to
          Result (see §5.1). return result.
```

### 5.1 Trace ownership (exception-safe)

The temporary trace file is SDK-owned from allocation (step 4) through
final Result construction (step 10). On every failure path between
step 4 and the final `return result`, the SDK MUST unlink the trace
file before re-raising. This covers JSON parsing, strict value
validation, terminal conversion, rollup computation, Result
construction, and unexpected Python-level exceptions (an
`AttributeError` from a bug, a `KeyboardInterrupt`, etc.). Ownership
transfers to the Result ONLY on a successful `return result` with the
`weakref.finalize` handle already attached. There is no window where
the trace is both unparented and undeleted.

Implementation sketch:

```python
# pseudo-code
trace_tempfile = _allocate_trace_tempfile()      # step 4
try:
    # steps 5 through 9
    _spawn_and_drain_with_caps(binary, args, trace_tempfile, timeout)
    _unlink(input_tempfile)                      # step 6, in its own finally
    _apply_nonzero_exit_rule(proc_result)        # step 7
    terminals = _parse_and_strict_validate(stdout)   # step 8
    rollup = _rollup(terminals)                  # step 9
    result = Result(                             # step 10
        terminals=tuple(terminals),
        result_type=rollup,
        trace_path=trace_tempfile,
    )
    finalize_handle = weakref.finalize(result, _unlink_quiet, trace_tempfile)
    result._finalize_handle = finalize_handle
except BaseException:
    # ANY failure path: SDK-defined (NCPSDKError and subclasses),
    # unexpected Python errors, KeyboardInterrupt. The trace file is
    # still SDK-owned at this point; unlink before re-raising.
    _unlink_quiet(trace_tempfile)
    raise
else:
    return result
```

The `except BaseException` scope is deliberate: an unexpected
implementation bug in step 8 (for example, a Python-level
`AttributeError` in the strict-validation code path) or a
`KeyboardInterrupt` must also clean up the trace. The Result
constructor itself must not raise AFTER it has conceptually assumed
ownership; `Result(...)` and the `weakref.finalize` attachment are an
atomic pair (both complete before `return result`), so a failure in
either leaves the trace in SDK ownership and the except branch handles
it.

The same ownership-transfer discipline is covered by a PR 2 test that
patches each of steps 7 to 10 to raise an unexpected exception and
asserts the trace tempfile is unlinked.

---

## 6. Exception hierarchy

```
Exception
+- NCPSDKError                     # base; SDK-side failures only
   +- NCPBinaryNotFoundError       # binary resolution precedence exhausted
   +- NCPRuntimeVersionError       # version not in SUPPORTED_RUNTIME_VERSIONS
   +- NCPTimeoutError              # subprocess exceeded timeout
   +- NCPSubprocessError           # see §4.3 and §4.4 rule 3
   +- NCPTraceSchemaError          # schema mismatch, malformed known
                                   # record, missing or unreadable
                                   # trace, or empty trace after a
                                   # successful subprocess (§8)
```

**`NCPSubprocessError` public attributes:**

- `.category` (str): one of `"nonzero_exit"`, `"malformed_stdout"`,
  `"strict_validation_failed"`, `"output_cap_exceeded"`.
- `.exit_code` (int or None): None when the process did not exit
  cleanly before the cap/timeout branch.
- `.binary_path` (Path): resolved binary used.
- `.stderr` (bytes): full captured stderr, bounded by §4.4 rule 8.
- `.args` / `__str__`: category, exit code, binary path, stderr
  length. NO stderr contents, NO input dict. See §4.4 rule 3.

**The SDK-vs-graph-failure split is a hard rule.** Graph-level outcomes
(including `result_type == "Failure"`) are NORMAL returns and do NOT
raise. Only infrastructure problems raise.

---

## 7. Rollup rule for `Result.result_type`

```
any Failure            -> "Failure"
else any LowConfidence -> "LowConfidence"
else                   -> "Success"
```

**Parity requirement.** The same inputs invoked through the MCP adapter
must produce the same rollup string. PR 3A.4-3 ships a parity test.

---

## 8. Trace iterator semantics

- **File opened lazily.** `Result.trace()` reopens the file each call.
- **Schema version.** Expected: exactly `"ncp-trace-0.1"`. Mismatch
  anywhere raises `NCPTraceSchemaError`. The SDK does NOT silently
  accept arbitrary future schema versions.
- **Forward compatibility within an accepted schema version.** Unknown
  `type` fields WITHIN an accepted schema version are yielded as
  `UnknownTraceRecord(type, raw)`. Forward compatibility applies ONLY
  to new record types within an accepted schema version, not to new
  schema versions.
- **Known `type` with missing required field** or wrong field type:
  raises `NCPTraceSchemaError` naming the record type and field.
- **Empty trace file after a successful subprocess execution.** Treated
  as a trace-write failure, NOT a legitimate zero-record outcome. The
  Rust runtime emits a `runtime_info` record on every execution
  ([`runtime/src/trace.rs`](../runtime/src/trace.rs)). First call to
  `.trace()` under this condition raises `NCPTraceSchemaError`.
- **Blank or whitespace-only lines interspersed with real records.**
  Silently skipped.
- **Invalid JSON on a line:** raises `NCPTraceSchemaError` with the
  line number.
- **Missing or unreadable trace file at `.trace()` call time.** Raises
  `NCPTraceSchemaError` with a message distinguishing it from a
  schema-parsing failure. Calling `.trace()` AFTER `.cleanup()` or
  context exit raises `FileNotFoundError`.
- **Cleanup reliability on parse failure.** If `.trace()` raises
  mid-iteration, the iterator MUST close its file handle before
  raising. A subsequent `.cleanup()` call MUST succeed regardless.
  PR 2 ships a test asserting both the raise and the clean unlink.

---

## 9. Workspace layout, SemVer, versioning policy

**Repo layout (git-tracked content only).**

```
python/ncp-runtime/
+- pyproject.toml              # hatchling build; Python >=3.10; see §11
+- README.md
+- CHANGELOG.md
+- LICENSE                     # Apache-2.0
+- NOTICE
+- scripts/
|  +- retag_wheel.py           # custom retagger; see §11 (if needed)
+- src/ncp_runtime/
|  +- __init__.py              # public re-exports + __all__
|  +- _version.py              # __version__ = "0.1.0"
|  +- types.py                 # Result, Terminal, TerminalError, exceptions
|  +- runner.py                # NCP class + spawn + parse
|  +- trace.py                 # Trace record dataclasses + iterator
|  +- discovery.py             # binary-discovery precedence + version check
|  +- demo.py                  # python -m ncp_runtime.demo entry point
|  +- py.typed                 # PEP 561 marker (empty file)
|  +- _bundled/
|  |  +- .gitkeep              # directory placeholder; wheel contents in §11
|  +- _fixtures/               # bundled minimal graph + brick artifacts
|     +- graph.yaml
|     +- <brick dirs>
+- tests/
   +- conftest.py              # NCP_RUNTIME env fixture
   +- test_smoke.py
   +- test_runner.py
   +- test_trace.py
   +- test_discovery.py
   +- test_demo.py
   +- test_wheel_contents.py   # §11 wheel-contents + exec-permission tests
   +- test_trace_ownership.py  # §5.1 ownership-transfer tests
   +- fixtures/
      +- low_confidence_stdout.json
      +- ...
```

`_bundled/` contains only `.gitkeep` in git. The platform binary and
`provenance.json` are produced at wheel-build time (§11). The sdist
ships `_bundled/.gitkeep` only.

**SemVer policy.** Independent of the Rust crate's version. Breaking
changes to the public Python API trigger a major-version bump. Adding
a runtime version to `SUPPORTED_RUNTIME_VERSIONS` is a minor-version
bump (or patch if the previous entry stays supported). Trace schema
VERSION changes trigger a minor-version bump that extends accepted
schema versions. New record TYPES within an accepted schema version
do not require an SDK release.

**Version lockstep.** `pyproject.toml`, `_version.py`, and
`CHANGELOG.md` move together at PR 3A.4-4.

---

## 10. Dependency on the `ncp` binary

**Discovery precedence** (locked in §3.1):

1. `binary` kwarg.
2. Bundled wheel binary.
3. `shutil.which("ncp")`.
4. `NCPBinaryNotFoundError`.

**Per-instance caching.** On first `.run()`, the resolved binary PATH
(not just the version) is captured on the NCP instance; the `--version`
check runs once and is cached. Subsequent calls on the same instance
reuse the cached path and version. Users who need to switch binaries
construct a new NCP instance.

**No auto-install.** No `cargo install`, no network at import or
runtime, no silent Rust-source fallback.

**`NCPBinaryNotFoundError` message content.** MUST:

- Name all three precedence options.
- Distinguish "Python-only installation (no bundled runtime)" from a
  generic lookup failure. The fallback wheel ships
  `ncp_runtime/_bundled/.no_binary`; `discovery.py` reads this marker.
- Name concrete remediation: `cargo install ncp-runtime --locked`, the
  prebuilt-binary release URL, or `NCP(binary="/path/to/custom/ncp")`.
- Not include raw environment data.

**Future `NCP(persistent=True)` sketch** (NOT implemented in v0.1.0):

```python
# Future: v0.2.x
class NCP:
    def __init__(
        self,
        *,
        binary: str | Path | None = None,
        timeout: float = 60.0,
        persistent: bool = False,
    ) -> None: ...
```

Default stays `persistent=False`. Hardening and nonzero-exit rules
apply unchanged. Public surfaces do not change.

---

## 11. Wheel-bundling strategy

**Goal.** `pip install ncp-runtime` on supported platforms produces an
immediately-usable installation.

**Build approach.** Custom GitHub Actions matrix. NOT `cibuildwheel`.

**Platform targets for v0.1.0.**

| Platform | Wheel tag | Build environment |
|---|---|---|
| Linux x86-64 | `py3-none-manylinux_2_28_x86_64` | `quay.io/pypa/manylinux_2_28_x86_64` container |
| macOS ARM64 | `py3-none-macosx_11_0_arm64` | macOS 14 runner |
| macOS x86-64 | `py3-none-macosx_10_13_x86_64` | macOS 14 runner, cross-build target |
| Windows x86-64 | `py3-none-win_amd64` | Windows 2022 runner |
| Universal fallback | `py3-none-any` | Any runner; no bundled binary |

**Per-platform job steps:**

1. Build release `ncp` binary (`cargo build -p ncp-runtime --release --locked --bin ncp`).
2. Copy the binary into `python/ncp-runtime/src/ncp_runtime/_bundled/ncp[.exe]`
   with POSIX mode 0755 (chmod explicitly on POSIX).
3. Generate `_bundled/provenance.json` (schema below).
4. Run `python -m build --wheel`.
5. Re-tag the resulting wheel with full metadata and RECORD integrity.
   Preferred tool: `wheel tags --platform-tag <tag>`. Fallback:
   `scripts/retag_wheel.py`.
6. Run `twine check` on each wheel before upload.

**Wheel contents + executable permissions.**

`tool.hatch.build.targets.wheel` config MUST include in every wheel's
sources:

- `src/ncp_runtime/_bundled/ncp[.exe]` on platform wheels.
- `src/ncp_runtime/_bundled/provenance.json` on every wheel.
- `src/ncp_runtime/_bundled/.no_binary` on the fallback wheel only.
- `src/ncp_runtime/_fixtures/graph.yaml` and every file under
  `_fixtures/` on every wheel.
- `src/ncp_runtime/py.typed` on every wheel.

The bundled binary on POSIX wheels MUST carry mode 0755 in the wheel's
internal file entries so `pip install` extracts it as executable.
`retag_wheel.py` MUST preserve those bits.

**`test_wheel_contents.py` (PR 2).** For each produced wheel:

- Extract into a temp directory.
- Assert `ncp_runtime/py.typed` is present.
- Assert `ncp_runtime/_fixtures/graph.yaml` is present AND readable.
- Platform wheels: assert `ncp_runtime/_bundled/ncp[.exe]` is present
  AND, on POSIX, that its mode has owner-execute set.
- Assert `ncp_runtime/_bundled/provenance.json` parses as JSON and has
  the required keys.
- Fallback wheel: `_bundled/.no_binary` present and `binary_sha256` is
  null in `provenance.json`.

**Binary provenance (every wheel).**

```json
{
  "sdk_version": "0.1.0",
  "runtime_source_commit": "<40-char git SHA of the ncp-runtime crate build>",
  "runtime_version": "0.3.6",
  "rust_toolchain": "1.94.0",
  "build_target_triple": "x86_64-unknown-linux-gnu",
  "wheel_platform_tag": "manylinux_2_28_x86_64",
  "binary_filename": "ncp",
  "binary_sha256": "sha256:<64-hex>",
  "binary_size_bytes": 12345678,
  "build_timestamp_rfc3339": "2026-10-26T14:00:00Z",
  "linux_dt_needed_baseline": ["libc.so.6", "libgcc_s.so.1", "..."]
}
```

- `runtime_source_commit` from `${{ github.sha }}` at build time.
- `rust_toolchain` matches the pinned toolchain (1.94.0).
- `binary_sha256` computed in-job after `cargo build`.
- `linux_dt_needed_baseline` Linux-only.
- Fallback wheel: `binary_sha256: null`, `runtime_source_commit: null`,
  `build_target_triple: null`, plus a `note` field.

**PR 4 acceptance criterion.** Every published platform wheel's
`provenance.json` ties the bundled binary back to source commit, Rust
toolchain, build target, and SHA-256. Ceremony blocks on all five
fields being populated.

**Linux-specific hardening.**

- Build inside `quay.io/pypa/manylinux_2_28_x86_64`. Target:
  `x86_64-unknown-linux-gnu`.
- Direct ELF verification:
  - `readelf -l <ncp>` for the ELF program interpreter (`PT_INTERP`),
    expected `/lib64/ld-linux-x86-64.so.2`.
  - `readelf -d <ncp>` for DT_NEEDED. Compared against the baseline in
    `provenance.json`; drift requires explicit review.
  - `readelf -V <ncp> | grep GLIBC_` for required GLIBC symbol
    versions; max MUST be `<= 2.28`.
  - `ldd <ncp>` inside a minimal glibc-2.28 image.
  - `auditwheel show` as secondary cross-check.

**macOS-specific notes.** Deployment target: 10.13 (x86-64) /
11.0 (ARM64). No code-signing / notarization in v0.1.0.

**Windows-specific notes.** Target: `x86_64-pc-windows-msvc`. Verify
with `dumpbin /dependents` that the binary needs only system DLLs.

**Ceremony fallback.** If any platform-wheel build fails during PR 4
and cannot be resolved in the same PR, publish the `py3-none-any`
fallback wheel ONLY. Follow-up v0.1.1 PR addresses the issue.

**Pip wheel-selection test.** PR 3A.4-4 adds a test that exercises
pip's wheel-selection with overlapping wheels: on a target platform,
`pip install --find-links ./dist ncp-runtime` must install the
platform wheel; on a simulated unsupported platform, the fallback.

---

## 12. Demo command (`python -m ncp_runtime.demo`)

Onboarding smoke, NOT a second framework. Behavior:

- Resolve the binary via the standard precedence (§3.1); print which
  step succeeded.
- Execute the bundled fixture graph at `src/ncp_runtime/_fixtures/graph.yaml`.
- Print `result.result_type` and a one-line-per-terminal summary.
- Exit 0 on Success rollup; exit 1 on any other rollup or any
  `NCPSDKError`.

**No installed console script.** v0.1.0 does NOT add a
`[project.scripts]` entry. `python -m ncp_runtime.demo` works via
`demo.py`'s `__main__` guard. A console script can be added later
without a breaking change.

The demo graph is deliberately tiny (echo-style; wheel-size budget
~500 KB uncompressed for `_fixtures/`).

Keep `NCP().run(...)` as the primary API; demo is onboarding only.

---

## 13. v0 non-goals

- PyO3 or native Rust bindings.
- WASM reimplementation in Python.
- Brick authoring framework / helpers / `.ncpbrick` tarball format.
- Signing or verification tooling.
- Process pooling or long-lived runtime.
- Async execution.
- HTTP transport.
- New graph semantics or runtime features.
- Network downloads at import or runtime.
- `cargo install` auto-invocation.
- Installed console scripts (§12).
- A new runtime handshake protocol (strict validation in §4.1 is
  defensive parsing, not a protocol change).

The public API (§3) must preserve a future path to persistent
execution and other refinements without a breaking change.

---

## 14. PyPI distribution name + import module

- **Distribution name:** `ncp-runtime`
- **Python import module:** `ncp_runtime`
- **PyPI package description:** `Python SDK for Neural Computation
  Protocol (NCP). Execute deterministic graphs using the NCP runtime.`

**Availability checks performed during PR 1 (2026-10-26).** Two
distinct properties, understood separately:

1. **PyPI distribution-name availability (enforced by PyPI).** A query
   against the PyPI JSON API confirmed that no package currently
   publishes under `ncp-runtime` (nor the normalized form
   `ncp_runtime`). Verified 2026-10-26.
2. **Import-namespace collision (NOT enforced by PyPI).** PyPI does
   NOT enforce import-namespace uniqueness: multiple distributions can
   declare the same import package name, and pip installs the second
   over the first. For `ncp_runtime`, we searched PyPI metadata for
   distributions whose `top_level.txt` or
   `Modules-Provides-Dist` declares the import module `ncp_runtime`;
   none were found at the time of the check. This is best-effort, not
   enforced. If a future PyPI release introduces a conflicting
   `ncp_runtime` import, we would ship a release note calling out the
   conflict.

**Re-verify at publication time.** PR 4 re-runs BOTH checks as the
first step of the ceremony and halts if either regresses.

**PyPI listing content.** README and PyPI package description must
prominently use the full name "Neural Computation Protocol (NCP)" so
developers can distinguish this project from Aviz Networks' similarly
named `ncp-sdk` (which also uses the `ncp` import namespace, which is
why our import module is namespaced as `ncp_runtime`, not `ncp`). The
README's first paragraph MUST state that this Python package is the
SDK for the NCP runtime, not the Rust executable itself.

---

## 15. Tag pattern

`ncp-runtime-sdk-v*`. Example: `ncp-runtime-sdk-v0.1.0`.
`.github/workflows/publish-sdk.yml` triggers on this pattern. PR 4
implements the RC-first-on-TestPyPI then real-tag-on-PyPI ceremony.

---

## 16. CHANGELOG strategy

Keep-a-Changelog format. First entry `0.1.0`. Each entry names the
runtime versions in `SUPPORTED_RUNTIME_VERSIONS` for that release.
Included in both the sdist and all wheels.

---

## 17. Example

Runnable adoption example at `examples/python-sdk/lead-qualification-vs-llm/`
(not in sdist). Fair, measured, correctness-first comparison between
an LLM-driven lead-qualification workflow and the same workflow with
an NCP step. See the Phase 3A.4 plan's §Comparison fairness for the
non-negotiable disclosure and measurement requirements. PR 3A.4-3
scope; design doc remains stable regardless of when or how the example
ships.

---

## Change log of this design doc

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-10-26 | Initial freeze (PR 3A.4-1) |
