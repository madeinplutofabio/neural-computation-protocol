#!/usr/bin/env python3
"""PR 3A.2-D — CI smoke for ncp-mcp-server.

Drives the release-built `ncp-mcp-server` binary as a subprocess and
verifies the end-to-end MCP stdio dialog against the echo-pipeline
example graph. This script is what the `mcp-smoke` job in
`.github/workflows/rust.yml` runs.

Scope (per docs/MCP_ADAPTER.md): happy-path only. The full unit-of-PR-C
protocol surface (unknown-tool errors, malformed-JSON errors,
concurrent dispatch) is covered by `tests/mcp_protocol.rs`. This
smoke verifies the adopter path: build → start → handshake →
tools/list → tools/call → verify §5 response shape end-to-end → close
stdin → assert the subprocess exits cleanly.

The post-dialog exit-status gate is mandatory: rmcp's graceful-shutdown
drain path (`rmcp/src/service.rs:1061`) requires the tokio time driver,
and a missing `.enable_time()` panics the server only on stdin EOF —
AFTER the last response has been sent. The dialog-only assertions
would print SMOKE OK and miss the panic. This script asserts a clean
exit code before printing SMOKE OK; if the subprocess exits non-zero
after stdin close, the smoke fails with a clear message and the
stderr tail. Mirrors `tests/mcp_protocol.rs::graceful_shutdown_does_not_panic`.

Dependencies: Python stdlib only — subprocess, json, sys, pathlib,
tempfile, threading.

Cross-platform: timeout enforcement uses threading.Timer (rather than
signal.SIGALRM) so the script runs on Linux/macOS/Windows. CI pins
ubuntu-24.04; adopters can run the same script locally on any
platform after building the binary for their host.

Timeout discipline: when the timer fires, it kills the subprocess.
Both stdout reads (`read_line`) AND stdin writes (`send`) wrap the
I/O in try/except + a `timeout_state["fired"]` check so a mid-write
kill surfaces as `SMOKE FAIL: ... subprocess killed by timer`
rather than an unhandled Python traceback.

Subprocess stderr discipline: stderr is redirected to a temp file
(NOT `subprocess.DEVNULL` or `subprocess.PIPE`). File redirection
cannot fill a pipe buffer (kernel writes go straight to disk), so
there is no deadlock hazard if the subprocess writes verbosely. On
failure, `fail()` prints the last few KB of that log to stderr so
CI logs carry diagnostics without a separate artifact-collection
step. The temp file is unlinked on success and left on disk on
failure for further inspection.

Usage (from workspace root, after `cargo build -p ncp-mcp-server
--release --locked`):

    python3 examples/mcp/ci_smoke.py

Exit codes:
- 0 → all assertions passed; prints `SMOKE OK` to stdout.
- 1 → an assertion failed or the script timed out; prints
  `SMOKE FAIL: <reason>` to stderr, plus the tail of the subprocess
  stderr log if one was captured. The subprocess is killed in a
  `finally` block whether the script exits cleanly, asserts, or
  times out.
"""

import json
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

# Overall script timeout. The timer kills the subprocess when it fires;
# any pending readline() returns empty string and any pending write
# raises BrokenPipeError/OSError, both of which read_line() / send()
# turn into a clear timeout error. Sized to be generous against CI
# cold-start jitter while still failing fast on real hangs.
SCRIPT_TIMEOUT_SECS = 60

# How much of the subprocess stderr log to dump on failure. 4 KiB is
# generous for the v0 adapter's stderr (one startup summary line plus
# any panic message); large enough to be useful, small enough to keep
# CI log noise bounded.
STDERR_TAIL_BYTES = 4096

# Set by main() so fail() can include subprocess stderr diagnostics
# in CI failure output. None when no log has been opened yet (e.g.
# if `fail()` runs from the pre-flight path-existence checks).
_STDERR_LOG_PATH: Path | None = None


def fail(message: str) -> None:
    """Print a smoke-failure message to stderr and exit 1.

    If a subprocess stderr log was captured, dump the tail to stderr
    to give CI logs and adopter terminals diagnostic context without
    a separate artifact-collection step.
    """
    print(f"SMOKE FAIL: {message}", file=sys.stderr)
    if _STDERR_LOG_PATH is not None and _STDERR_LOG_PATH.exists():
        try:
            data = _STDERR_LOG_PATH.read_text(errors="replace")
            if data:
                tail = data[-STDERR_TAIL_BYTES:]
                print("--- subprocess stderr (tail) ---", file=sys.stderr)
                print(tail.rstrip(), file=sys.stderr)
                print("--- end stderr ---", file=sys.stderr)
        except OSError:
            # Log file unreadable; not worth a second-level error.
            pass
    sys.exit(1)


def send(proc: subprocess.Popen, frame: dict, timeout_state: dict) -> None:
    """Write one JSON-RPC frame (one line + LF) to the subprocess stdin.

    Wraps write/flush in try/except so that if the timer kills the
    subprocess mid-write (causing BrokenPipeError on Unix or OSError
    on Windows when stdin closes), we surface a clean `SMOKE FAIL: ...`
    line rather than letting an unhandled exception print a Python
    traceback. The `timeout_state` container distinguishes "timer
    fired" from a genuine I/O error so the failure message points at
    the real cause.
    """
    line = json.dumps(frame) + "\n"
    try:
        proc.stdin.write(line)
        proc.stdin.flush()
    except (BrokenPipeError, OSError) as e:
        if timeout_state["fired"]:
            fail(
                f"smoke script exceeded SCRIPT_TIMEOUT_SECS="
                f"{SCRIPT_TIMEOUT_SECS} (subprocess killed by timer)"
            )
        fail(f"failed writing JSON-RPC frame to subprocess stdin: {e}")


def read_line(proc: subprocess.Popen, timeout_state: dict) -> dict:
    """Read one line from subprocess stdout and parse as JSON.

    `timeout_state` is the shared mutable container the timer
    populates; if `readline` returns empty AFTER the timer fired,
    we surface a timeout failure rather than the generic
    "subprocess closed stdout" message.
    """
    line = proc.stdout.readline()
    if not line:
        if timeout_state["fired"]:
            fail(
                f"smoke script exceeded SCRIPT_TIMEOUT_SECS="
                f"{SCRIPT_TIMEOUT_SECS} (subprocess killed by timer)"
            )
        fail("subprocess closed stdout before sending a frame")
    try:
        return json.loads(line)
    except json.JSONDecodeError as e:
        fail(f"stdout line is not valid JSON: {e}\n  line: {line!r}")


def assert_jsonrpc(resp: dict, expected_id, context: str) -> None:
    """Common JSON-RPC 2.0 envelope assertions."""
    if resp.get("jsonrpc") != "2.0":
        fail(f"{context}: response missing jsonrpc='2.0': {resp}")
    if expected_id is not None and resp.get("id") != expected_id:
        fail(f"{context}: response id mismatch (expected {expected_id}): {resp}")
    if resp.get("error") is not None:
        fail(f"{context}: response carried JSON-RPC error: {resp}")


def run_smoke(proc: subprocess.Popen, timeout_state: dict) -> None:
    """Execute the full happy-path dialog and assert against §5 shape."""
    # ── initialize ────────────────────────────────────────────────
    send(proc, {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {
                "name": "ncp-mcp-server-ci-smoke",
                "version": "0.1.0",
            },
        },
    }, timeout_state)
    init_resp = read_line(proc, timeout_state)
    assert_jsonrpc(init_resp, expected_id=1, context="initialize")
    result = init_resp.get("result")
    if not isinstance(result, dict):
        fail(f"initialize response has no result object: {init_resp}")
    tools_cap = result.get("capabilities", {}).get("tools")
    if not isinstance(tools_cap, dict):
        fail(f"initialize result missing capabilities.tools: {init_resp}")
    # §13: listChanged is false or absent (we emit it as false; absent
    # is also acceptable for forward compatibility).
    if tools_cap.get("listChanged", False) is not False:
        fail(
            f"capabilities.tools.listChanged must be false or absent per §13: "
            f"{tools_cap}"
        )

    # ── notifications/initialized ─────────────────────────────────
    send(proc, {
        "jsonrpc": "2.0",
        "method": "notifications/initialized",
    }, timeout_state)

    # ── tools/list ────────────────────────────────────────────────
    send(proc, {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {},
    }, timeout_state)
    list_resp = read_line(proc, timeout_state)
    assert_jsonrpc(list_resp, expected_id=2, context="tools/list")
    tools = list_resp.get("result", {}).get("tools", [])
    if not isinstance(tools, list) or len(tools) == 0:
        fail(f"tools/list returned empty tools array: {list_resp}")
    tool_names = [t.get("name") for t in tools]
    expected_name = "org.ncp-examples.echo-pipeline"
    if expected_name not in tool_names:
        fail(f"{expected_name!r} not in tools/list: {tool_names}")
    echo_tool = next(t for t in tools if t.get("name") == expected_name)
    schema_type = echo_tool.get("inputSchema", {}).get("type")
    if schema_type != "object":
        fail(
            f"echo-pipeline inputSchema.type must be 'object' per §4, "
            f"got {schema_type!r}: {echo_tool}"
        )

    # ── tools/call ────────────────────────────────────────────────
    send(proc, {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": expected_name,
            "arguments": {"smoke": "hello"},
        },
    }, timeout_state)
    call_resp = read_line(proc, timeout_state)
    assert_jsonrpc(call_resp, expected_id=3, context="tools/call")
    call_result = call_resp.get("result")
    if not isinstance(call_result, dict):
        fail(f"tools/call response has no result object: {call_resp}")
    if call_result.get("isError") is not False:
        fail(
            f"tools/call result.isError must be false on Class A Success: "
            f"{call_result}"
        )
    sc = call_result.get("structuredContent")
    if not isinstance(sc, dict):
        fail(f"tools/call result missing structuredContent: {call_result}")
    if sc.get("result_type") != "Success":
        fail(f"structuredContent.result_type must be 'Success': {sc}")
    if not isinstance(sc.get("trace_id"), str):
        fail(f"structuredContent.trace_id must be a string: {sc}")
    if not isinstance(sc.get("trace_path"), str):
        fail(
            f"structuredContent.trace_path must be a string when --trace-dir "
            f"is set: {sc}"
        )
    if "output_json" not in sc:
        fail(f"structuredContent.output_json must be present: {sc}")

    # ── trace file exists + non-empty ─────────────────────────────
    trace_path = Path(sc["trace_path"])
    if not trace_path.exists():
        fail(f"trace file does not exist on disk: {trace_path}")
    if trace_path.stat().st_size == 0:
        fail(f"trace file is empty: {trace_path}")

    # ── text content mirror equals structuredContent ──────────────
    content = call_result.get("content", [])
    if not isinstance(content, list) or len(content) != 1:
        fail(f"result.content must be a single-item array: {content}")
    if content[0].get("type") != "text":
        fail(f"content[0].type must be 'text': {content[0]}")
    try:
        mirror = json.loads(content[0]["text"])
    except (json.JSONDecodeError, KeyError) as e:
        fail(f"content[0].text must parse as JSON: {e}")
    if mirror != sc:
        fail(
            "content[0].text must equal structuredContent "
            "(JSON-serialized mirror per §5):\n"
            f"  mirror:            {mirror}\n"
            f"  structuredContent: {sc}"
        )


def main() -> None:
    global _STDERR_LOG_PATH

    workspace = Path(__file__).resolve().parent.parent.parent
    binary = workspace / "target" / "release" / "ncp-mcp-server"
    # Windows: cargo emits `ncp-mcp-server.exe`. Add the suffix so
    # adopters running this script locally on Windows get a clean
    # "release binary not found" message rather than a silent
    # cross-host path mismatch.
    if sys.platform == "win32":
        binary = binary.with_suffix(".exe")
    graph = workspace / "examples" / "graphs" / "echo-pipeline" / "graph.yaml"
    brick_dir = workspace / "examples" / "bricks"

    for path, label in [
        (binary, "release binary"),
        (graph, "echo-pipeline graph"),
        (brick_dir, "brick dir"),
    ]:
        if not path.exists():
            fail(
                f"{label} not found: {path}\n"
                f"  (did you run "
                f"`cargo build -p ncp-mcp-server --release --locked` first?)"
            )

    trace_dir = Path(tempfile.mkdtemp(prefix="ncp-mcp-smoke-"))

    # Open subprocess stderr log. File redirection avoids the pipe-
    # buffer deadlock hazard of `subprocess.PIPE` (no buffer to fill;
    # the kernel writes straight to disk) while still giving us
    # diagnostics on CI failures via fail()'s tail dump. Opening in
    # binary mode keeps the encoding decision in our hands at
    # read-time via `read_text(errors="replace")`.
    stderr_log = tempfile.NamedTemporaryFile(
        mode="wb",
        prefix="ncp-mcp-smoke-stderr-",
        suffix=".log",
        delete=False,
    )
    _STDERR_LOG_PATH = Path(stderr_log.name)

    proc = subprocess.Popen(
        [
            str(binary),
            "--graph",
            str(graph),
            "--brick-dir",
            str(brick_dir),
            "--trace-dir",
            str(trace_dir),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=stderr_log,
        text=True,  # text mode applies to stdin/stdout; stderr is binary file.
        bufsize=1,  # line-buffered text I/O on stdin/stdout
    )

    # Cross-platform timeout. When the timer fires, the subprocess is
    # killed; pending stdout reads then return empty string and pending
    # stdin writes raise BrokenPipeError/OSError. Both code paths
    # check this shared `timeout_state["fired"]` flag and surface a
    # clear timeout failure message.
    timeout_state = {"fired": False}

    def on_timeout() -> None:
        timeout_state["fired"] = True
        try:
            proc.kill()
        except OSError:
            # Already exited; nothing to kill.
            pass

    timer = threading.Timer(SCRIPT_TIMEOUT_SECS, on_timeout)
    timer.daemon = True
    timer.start()

    success = False
    try:
        # 1. Run the JSON-RPC dialog and assert §5 response shape.
        run_smoke(proc, timeout_state)

        # 2. Graceful-shutdown gate: close stdin, wait for the child
        # to exit on its own, assert a clean exit code. Without this
        # gate, the script would print SMOKE OK as soon as the dialog
        # succeeded — even if the server then panicked on stdin EOF
        # in rmcp's drain path (rmcp/src/service.rs:1061, which
        # requires the tokio time driver). The Phase 3A.2-E real-host
        # validation gate caught that exact bug; this in-script gate
        # is the regression sentinel against re-introduction. Mirrors
        # tests/mcp_protocol.rs::graceful_shutdown_does_not_panic.
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        try:
            returncode = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            fail(
                "subprocess did not exit within 10s after stdin EOF "
                "(graceful shutdown stalled)"
            )
        if returncode != 0:
            fail(
                f"subprocess exited non-zero after graceful shutdown: "
                f"returncode={returncode}"
            )

        print("SMOKE OK")
        success = True
    finally:
        timer.cancel()
        # If the child is still alive here, it's because run_smoke or
        # the graceful-shutdown gate raised before completing. Clean
        # up the runaway process; the stderr tail from fail() already
        # surfaced diagnostics.
        if proc.poll() is None:
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        try:
            stderr_log.close()
        except OSError:
            pass
        # On success, remove the stderr log to avoid tempfile sprawl
        # in adopters' /tmp. On failure (including assertion exits via
        # fail()), leave it on disk for further inspection beyond the
        # tail we already printed.
        if success and _STDERR_LOG_PATH is not None:
            try:
                _STDERR_LOG_PATH.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    main()
