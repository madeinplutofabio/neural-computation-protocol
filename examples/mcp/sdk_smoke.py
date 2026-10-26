#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori
"""Issue #32 regression sentinel: SDK-driven CI smoke for ncp-mcp-server.

Drives the release-built ncp-mcp-server through the official MCP Python
SDK (mcp.ClientSession) and complements examples/mcp/ci_smoke.py (which
exercises the same end-to-end dialog over raw stdlib JSON-RPC). Where
ci_smoke.py validates the server's wire protocol against a hand-rolled
client, this script validates it against the actual client library an
adopter would use, so SDK framing changes, protocol-version negotiation
drift, or typed-model mismatches cannot slip through unnoticed.

The script also enforces a graceful-shutdown exit-code gate, which is
the regression sentinel for issue #25 (rmcp panic on stdin EOF without
a running tokio time driver). That bug surfaces only AFTER the final
response has been sent, so a dialog-only test prints OK and misses it.

Transport note (per issue #32 decisions):

The SDK's mcp.client.stdio.stdio_client manages the subprocess
internally and does not expose the Process object; its own cleanup also
force-terminates after a 2-second budget. Both facts make it unsuitable
for an exit-code assertion with a 10-second shutdown window. This script
therefore spawns the subprocess directly via the public anyio.open_process
and bridges its byte streams to a public mcp.ClientSession using only
documented types (mcp.types.JSONRPCMessage, mcp.shared.message.SessionMessage,
anyio.create_memory_object_stream, anyio.streams.text.TextReceiveStream).
This validates the SDK's session handling, protocol negotiation, and typed
responses; it does not independently exercise the SDK's own stdio_client
transport (that is a separate concern).

Scope: happy-path only, mirroring ci_smoke.py's section-5 assertion set
(see docs/MCP_ADAPTER.md section 5 for the Class A response-shape contract).
Error-class protocol surface stays in tests/mcp_protocol.rs.

Dependencies:
- Python 3.10+
- mcp == 1.27.0 (pinned)
- anyio (transitive via mcp)

Usage (from workspace root, after
`cargo build -p ncp-mcp-server --release --locked`):

    pip install mcp==1.27.0
    python examples/mcp/sdk_smoke.py

Exit codes:
- 0 on success; prints `SDK SMOKE OK`.
- 1 on any assertion failure or timeout; prints
  `SDK SMOKE FAIL: <reason>` to stderr followed by the tail of the
  subprocess stderr log.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NoReturn

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from anyio.streams.text import TextReceiveStream
from mcp import ClientSession
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage

SCRIPT_TIMEOUT_SECS = 60
SHUTDOWN_TIMEOUT_SECS = 10
TERMINATE_TIMEOUT_SECS = 5
STDERR_TAIL_BYTES = 4096
EXPECTED_TOOL_NAME = "org.ncp-examples.echo-pipeline"


class SmokeFailure(Exception):
    """Raised by check()/fail() to abort the smoke run with a message."""


def fail(message: str) -> NoReturn:
    raise SmokeFailure(message)


def check(cond: bool, context: str) -> None:
    if not cond:
        fail(context)


async def run_dialog(session: ClientSession) -> None:
    """Execute initialize, tools/list, and tools/call with full shape checks."""
    init_result = await session.initialize()
    check(
        init_result.serverInfo.name == "ncp-mcp-server",
        f"serverInfo.name must be 'ncp-mcp-server', got "
        f"{init_result.serverInfo.name!r}",
    )
    check(
        init_result.capabilities.tools is not None,
        "capabilities.tools must be present per MCP_ADAPTER section 13",
    )
    tools_cap = init_result.capabilities.tools
    list_changed = getattr(tools_cap, "listChanged", False)
    check(
        list_changed is False or list_changed is None,
        f"capabilities.tools.listChanged must be false or absent per section 13, "
        f"got {list_changed!r}",
    )

    tools_result = await session.list_tools()
    check(
        len(tools_result.tools) > 0,
        "tools/list returned empty tools array",
    )
    tool_names = [t.name for t in tools_result.tools]
    check(
        EXPECTED_TOOL_NAME in tool_names,
        f"{EXPECTED_TOOL_NAME!r} not in tools/list: {tool_names}",
    )
    echo_tool = next(t for t in tools_result.tools if t.name == EXPECTED_TOOL_NAME)
    check(
        echo_tool.inputSchema.get("type") == "object",
        f"echo-pipeline inputSchema.type must be 'object' per section 4, "
        f"got {echo_tool.inputSchema.get('type')!r}",
    )

    call_result = await session.call_tool(
        EXPECTED_TOOL_NAME,
        arguments={"smoke": "hello"},
    )
    check(
        call_result.isError is False,
        f"tools/call result.isError must be false on Class A Success, "
        f"got {call_result.isError!r}",
    )
    sc = call_result.structuredContent
    check(
        isinstance(sc, dict),
        "tools/call result missing structuredContent",
    )
    check(
        sc.get("result_type") == "Success",
        f"structuredContent.result_type must be 'Success', "
        f"got {sc.get('result_type')!r}",
    )
    check(
        isinstance(sc.get("trace_id"), str),
        "structuredContent.trace_id must be a string",
    )
    check(
        isinstance(sc.get("trace_path"), str),
        "structuredContent.trace_path must be a string when --trace-dir is set",
    )
    check(
        "output_json" in sc,
        "structuredContent.output_json must be present",
    )

    trace_path = Path(sc["trace_path"])
    check(
        trace_path.exists(),
        f"trace file does not exist on disk: {trace_path}",
    )
    check(
        trace_path.stat().st_size > 0,
        f"trace file is empty: {trace_path}",
    )

    content = call_result.content
    check(
        len(content) == 1 and content[0].type == "text",
        "result.content must be a single text item per section 5",
    )
    try:
        mirror = json.loads(content[0].text)
    except json.JSONDecodeError as e:
        fail(f"content[0].text must parse as JSON: {e}")
    check(
        mirror == sc,
        "content[0].text must equal structuredContent (JSON mirror per section 5)",
    )


async def _run_smoke(
    binary: Path,
    graph: Path,
    brick_dir: Path,
    trace_dir: Path,
    stderr_fd: int,
) -> int:
    """Spawn, run the SDK dialog, close stdin, and return the subprocess exit code.

    Public-API transport bridge: byte streams on the proc are wrapped as
    memory object streams carrying SessionMessage and handed to ClientSession.
    """
    proc = await anyio.open_process(
        [
            str(binary),
            "--graph", str(graph),
            "--brick-dir", str(brick_dir),
            "--trace-dir", str(trace_dir),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=stderr_fd,
    )

    try:
        read_writer: MemoryObjectSendStream[SessionMessage | Exception]
        read_reader: MemoryObjectReceiveStream[SessionMessage | Exception]
        write_writer: MemoryObjectSendStream[SessionMessage]
        write_reader: MemoryObjectReceiveStream[SessionMessage]
        read_writer, read_reader = anyio.create_memory_object_stream(0)
        write_writer, write_reader = anyio.create_memory_object_stream(0)

        async def stdout_reader() -> None:
            assert proc.stdout is not None
            try:
                async with read_writer:
                    buffer = ""
                    async for chunk in TextReceiveStream(
                        proc.stdout, encoding="utf-8", errors="strict",
                    ):
                        lines = (buffer + chunk).split("\n")
                        buffer = lines.pop()
                        for line in lines:
                            if not line.strip():
                                continue
                            try:
                                message = JSONRPCMessage.model_validate_json(line)
                            except Exception as exc:
                                await read_writer.send(exc)
                                continue
                            await read_writer.send(SessionMessage(message))
            except anyio.ClosedResourceError:
                pass

        async def stdin_writer() -> None:
            assert proc.stdin is not None
            try:
                async with write_reader:
                    async for session_message in write_reader:
                        payload = session_message.message.model_dump_json(
                            by_alias=True, exclude_none=True,
                        )
                        await proc.stdin.send((payload + "\n").encode("utf-8"))
            except (anyio.ClosedResourceError, anyio.BrokenResourceError):
                pass

        async with anyio.create_task_group() as tg:
            tg.start_soon(stdout_reader)
            tg.start_soon(stdin_writer)

            async with ClientSession(read_reader, write_writer) as session:
                await run_dialog(session)

            # Explicit shutdown sequence (per issue #32 requirement):
            # (a) close the outgoing memory stream so stdin_writer drains and exits,
            # (b) explicitly close the subprocess stdin to send EOF,
            # (c) close the incoming memory stream (stdout_reader will also exit
            #     when proc.stdout closes).
            with anyio.CancelScope(shield=True):
                try:
                    await write_writer.aclose()
                except anyio.ClosedResourceError:
                    pass
                if proc.stdin is not None:
                    try:
                        await proc.stdin.aclose()
                    except Exception:
                        pass
                try:
                    await read_reader.aclose()
                except anyio.ClosedResourceError:
                    pass

        # Graceful-shutdown exit-code gate.
        try:
            with anyio.fail_after(SHUTDOWN_TIMEOUT_SECS):
                await proc.wait()
        except TimeoutError:
            fail(
                f"subprocess did not exit within {SHUTDOWN_TIMEOUT_SECS}s "
                f"after stdin EOF (graceful shutdown stalled)"
            )

        return proc.returncode if proc.returncode is not None else -1
    finally:
        # Ensure no orphan subprocess on any failure path.
        if proc.returncode is None:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                with anyio.fail_after(TERMINATE_TIMEOUT_SECS):
                    await proc.wait()
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
                try:
                    await proc.wait()
                except Exception:
                    pass


async def _async_entry(stderr_fd: int) -> None:
    workspace = Path(__file__).resolve().parent.parent.parent
    binary = workspace / "target" / "release" / "ncp-mcp-server"
    if sys.platform == "win32":
        binary = binary.with_suffix(".exe")
    graph = workspace / "examples" / "graphs" / "echo-pipeline" / "graph.yaml"
    brick_dir = workspace / "examples" / "bricks"

    for path, label in (
        (binary, "release binary"),
        (graph, "echo-pipeline graph"),
        (brick_dir, "brick dir"),
    ):
        if not path.exists():
            fail(
                f"{label} not found: {path} "
                f"(run `cargo build -p ncp-mcp-server --release --locked` first)"
            )

    trace_dir = Path(tempfile.mkdtemp(prefix="ncp-mcp-sdk-smoke-"))

    try:
        with anyio.fail_after(SCRIPT_TIMEOUT_SECS):
            rc = await _run_smoke(binary, graph, brick_dir, trace_dir, stderr_fd)
    except TimeoutError:
        fail(
            f"script exceeded SCRIPT_TIMEOUT_SECS={SCRIPT_TIMEOUT_SECS} "
            f"(did the subprocess hang?)"
        )

    if rc != 0:
        fail(
            f"subprocess exited non-zero after graceful shutdown: "
            f"returncode={rc}"
        )


def _find_smoke_failure(exc: BaseException) -> SmokeFailure | None:
    """Walk an exception (and any ExceptionGroup it carries) for a SmokeFailure.

    anyio task groups wrap raised exceptions in a (Base)ExceptionGroup. This
    helper unwraps that so the user sees the original failure message rather
    than the group boilerplate.
    """
    if isinstance(exc, SmokeFailure):
        return exc
    sub = getattr(exc, "exceptions", None)
    if sub is not None:
        for inner in sub:
            found = _find_smoke_failure(inner)
            if found is not None:
                return found
    return None


def _print_failure(message: str, stderr_path: Path) -> None:
    print(f"SDK SMOKE FAIL: {message}", file=sys.stderr)
    if stderr_path.exists():
        try:
            data = stderr_path.read_text(errors="replace")
            if data:
                tail = data[-STDERR_TAIL_BYTES:]
                print("--- subprocess stderr (tail) ---", file=sys.stderr)
                print(tail.rstrip(), file=sys.stderr)
                print("--- end stderr ---", file=sys.stderr)
        except OSError:
            pass


def main() -> None:
    stderr_log = tempfile.NamedTemporaryFile(
        mode="wb",
        prefix="ncp-mcp-sdk-smoke-stderr-",
        suffix=".log",
        delete=False,
    )
    stderr_path = Path(stderr_log.name)
    stderr_fd = stderr_log.fileno()

    try:
        try:
            anyio.run(_async_entry, stderr_fd)
        except BaseException as e:
            smoke = _find_smoke_failure(e)
            if smoke is not None:
                _print_failure(str(smoke), stderr_path)
            else:
                _print_failure(f"unexpected error: {e!r}", stderr_path)
            sys.exit(1)
    finally:
        try:
            stderr_log.close()
        except OSError:
            pass

    # Success path.
    try:
        stderr_path.unlink()
    except OSError:
        pass
    print("SDK SMOKE OK")


if __name__ == "__main__":
    main()
