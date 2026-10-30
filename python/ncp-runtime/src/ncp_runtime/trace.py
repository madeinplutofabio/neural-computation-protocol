# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Trace record dataclasses and the lazy JSONL iterator.

Implements design doc §3.3 (record variants) and §8 (iterator
semantics).

The Rust runtime emits one JSON object per line via
`runtime/src/trace.rs`, with a `type` field discriminating the record
kind. v0.1.0 of the SDK recognizes `runtime_info` and `invoke` and
yields `UnknownTraceRecord` for any other `type` seen within the
accepted schema version `ncp-trace-0.1` (forward compat per §8).

The iterator opens the file lazily on first consumption. Missing or
unreadable files at open or read time surface as
`NCPTraceSchemaError`; the post-cleanup contract-violation case is
handled by:
  * `Result.trace()` at call time (checks finalize-handle alive);
  * this iterator at first consumption (via optional `cleanup_check`
    callback) so a caller that defers consumption past `.cleanup()`
    still sees `FileNotFoundError` rather than `NCPTraceSchemaError`.
Both share one source of truth: `_finalize_handle.alive` on Result.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeAlias, TypeVar

from ncp_runtime.types import NCPTraceSchemaError

# Schema version the SDK accepts in v0.1.0. Any record whose
# `trace_schema_version` differs is rejected; see §8 and §4.2.
_ACCEPTED_SCHEMA_VERSION = "ncp-trace-0.1"

# Known `type` discriminator values at schema version ncp-trace-0.1.
# Must match the Rust-side emission in `runtime/src/trace.rs`.
_TYPE_RUNTIME_INFO = "runtime_info"
_TYPE_INVOKE = "invoke"


# --- Record variants ---------------------------------------------------


@dataclass(frozen=True)
class RuntimeInfoRecord:
    """Startup record emitted once at the top of every trace file. §3.3."""

    trace_schema_version: str
    runtime_version: str
    wasmtime_version: str
    timestamp: str


@dataclass(frozen=True)
class InvokeRecord:
    """One brick invocation record. §3.3."""

    trace_schema_version: str
    timestamp: str
    trace_id: str
    session_id: str
    step: int
    graph_id: str
    graph_version: str
    brick_id: str
    brick_version: str
    bundle_digest: str
    node_id: str
    trigger: dict[str, Any]
    input_hash: str
    output_hash: str | None
    result_len: int | None
    result_type: str
    latency_ms: float
    error_class: str | None


@dataclass(frozen=True)
class UnknownTraceRecord:
    """Forward-compat fallback for a `type` the SDK does not yet know
    about, within an accepted schema version. See §8.
    """

    type: str
    raw: dict[str, Any]


TraceRecord: TypeAlias = RuntimeInfoRecord | InvokeRecord | UnknownTraceRecord


# --- Field extraction helpers -----------------------------------------

T = TypeVar("T")


def _required(
    obj: dict[str, Any],
    key: str,
    expected: type[T],
    *,
    record_type: str,
    path: Path,
    line_number: int,
) -> T:
    """Return `obj[key]` after confirming it is of `expected` type.

    Raises NCPTraceSchemaError naming the record type, field, and
    line number if the key is absent or the value is of a wrong type.
    `expected is int` also rejects `bool` (which is a Python `int`
    subclass) so a JSON `true` cannot slip through as `step=1`.
    """
    if key not in obj:
        raise NCPTraceSchemaError(
            f"{record_type} record at {path} line {line_number} missing required field {key!r}"
        )
    value = obj[key]
    if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
        raise NCPTraceSchemaError(
            f"{record_type} record at {path} line {line_number} "
            f"field {key!r} has wrong type: expected {expected.__name__}, "
            f"got {type(value).__name__}"
        )
    # mypy narrows `value` to `T` after the isinstance guard; the
    # earlier `cast(T, value)` was redundant.
    return value


def _optional(
    obj: dict[str, Any],
    key: str,
    expected: type[T],
    *,
    record_type: str,
    path: Path,
    line_number: int,
) -> T | None:
    """Return `obj[key]` or None.

    Absent key or explicit null returns None. Present-but-wrong type
    raises NCPTraceSchemaError. `bool` rejected under `int` as above.
    """
    if key not in obj or obj[key] is None:
        return None
    value = obj[key]
    if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
        raise NCPTraceSchemaError(
            f"{record_type} record at {path} line {line_number} "
            f"field {key!r} has wrong type: expected {expected.__name__} or null, "
            f"got {type(value).__name__}"
        )
    # mypy narrows after isinstance; cast was redundant.
    return value


# --- Builders ---------------------------------------------------------


def _build_runtime_info(obj: dict[str, Any], path: Path, line_number: int) -> RuntimeInfoRecord:
    def req(key: str, expected: type[Any]) -> Any:
        return _required(
            obj,
            key,
            expected,
            record_type="runtime_info",
            path=path,
            line_number=line_number,
        )

    return RuntimeInfoRecord(
        trace_schema_version=req("trace_schema_version", str),
        runtime_version=req("runtime_version", str),
        wasmtime_version=req("wasmtime_version", str),
        timestamp=req("timestamp", str),
    )


def _build_invoke(obj: dict[str, Any], path: Path, line_number: int) -> InvokeRecord:
    def req(key: str, expected: type[Any]) -> Any:
        return _required(
            obj,
            key,
            expected,
            record_type="invoke",
            path=path,
            line_number=line_number,
        )

    def opt(key: str, expected: type[Any]) -> Any:
        return _optional(
            obj,
            key,
            expected,
            record_type="invoke",
            path=path,
            line_number=line_number,
        )

    # latency_ms: Rust emits f64; JSON may serialize an integer value
    # as `3` (int) or `3.0` (float). Accept both and normalize to float.
    if "latency_ms" not in obj:
        raise NCPTraceSchemaError(
            f"invoke record at {path} line {line_number} missing required field 'latency_ms'"
        )
    latency_raw = obj["latency_ms"]
    if isinstance(latency_raw, bool) or not isinstance(latency_raw, (int, float)):
        raise NCPTraceSchemaError(
            f"invoke record at {path} line {line_number} "
            f"field 'latency_ms' has wrong type: expected number, "
            f"got {type(latency_raw).__name__}"
        )
    latency_ms = float(latency_raw)

    return InvokeRecord(
        trace_schema_version=req("trace_schema_version", str),
        timestamp=req("timestamp", str),
        trace_id=req("trace_id", str),
        session_id=req("session_id", str),
        step=req("step", int),
        graph_id=req("graph_id", str),
        graph_version=req("graph_version", str),
        brick_id=req("brick_id", str),
        brick_version=req("brick_version", str),
        bundle_digest=req("bundle_digest", str),
        node_id=req("node_id", str),
        trigger=req("trigger", dict),
        input_hash=req("input_hash", str),
        output_hash=opt("output_hash", str),
        result_len=opt("result_len", int),
        result_type=req("result_type", str),
        latency_ms=latency_ms,
        error_class=opt("error_class", str),
    )


# --- Iterator ---------------------------------------------------------


def iter_trace_records(
    trace_path: Path,
    cleanup_check: Callable[[], bool] | None = None,
) -> Iterator[TraceRecord]:
    """Yield typed trace records from a JSONL trace file.

    Open happens lazily on first consumption. OSError at open or read
    time (missing file, permission denied, is-a-directory, filesystem
    I/O failure, etc.) is translated to NCPTraceSchemaError; the
    post-cleanup FileNotFoundError case is signalled by either (a)
    `Result.trace()` upstream at call time, or (b) `cleanup_check`
    returning False here at first consumption. Single source of
    truth: `_finalize_handle.alive` on Result, which `cleanup_check`
    observes via a captured reference.

    See design doc §8 for the full semantics:
      * Blank or whitespace-only lines are silently skipped.
      * Empty / whitespace-only file (no records at all) raises
        NCPTraceSchemaError: the runtime always emits a runtime_info
        record on a successful execution, so an empty trace indicates
        a trace-write failure.
      * Invalid JSON on a line raises NCPTraceSchemaError with the
        line number.
      * Schema version mismatch anywhere raises NCPTraceSchemaError.
      * Known record type with a missing or wrongly-typed required
        field raises NCPTraceSchemaError.
      * Unknown record type within an accepted schema version yields
        UnknownTraceRecord(type, raw).
      * Unknown EXTRA fields on a known record type (e.g.,
        `carry_state_hash` currently emitted but not in §3.3's
        InvokeRecord) are silently ignored, consistent with standard
        JSON "ignore unknown fields" practice.

    On any raise after the file is opened, the file handle is closed
    in the finally block before the exception propagates (cleanup
    reliability per §8).
    """
    # Post-cleanup contract-violation check at FIRST consumption.
    # Caller may have obtained the iterator before `.cleanup()` and
    # consumed it after; this covers that gap. Call-time check is
    # performed upstream by Result.trace(). Both share the same
    # finalize-handle.alive truth.
    if cleanup_check is not None and not cleanup_check():
        raise FileNotFoundError(
            f"trace() iterator consumed after cleanup; trace file no longer exists: {trace_path}"
        )

    try:
        f = trace_path.open("r", encoding="utf-8", newline="")
    except OSError as e:
        raise NCPTraceSchemaError(
            f"trace file not readable at expected path ({type(e).__name__}: {e}): {trace_path}"
        ) from e

    try:
        try:
            yielded_any = False
            for line_number, raw_line in enumerate(f, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as e:
                    raise NCPTraceSchemaError(
                        f"invalid JSON in trace file {trace_path} line {line_number}: {e.msg}"
                    ) from e
                if not isinstance(obj, dict):
                    raise NCPTraceSchemaError(
                        f"trace record at {trace_path} line {line_number} is not a JSON object: "
                        f"got {type(obj).__name__}"
                    )

                schema_version = obj.get("trace_schema_version")
                if schema_version != _ACCEPTED_SCHEMA_VERSION:
                    raise NCPTraceSchemaError(
                        f"trace schema version mismatch at {trace_path} line {line_number}: "
                        f"found {schema_version!r}, expected {_ACCEPTED_SCHEMA_VERSION!r}"
                    )

                rec_type = obj.get("type")
                if not isinstance(rec_type, str):
                    raise NCPTraceSchemaError(
                        f"trace record at {trace_path} line {line_number} "
                        f"missing or non-string 'type' field"
                    )

                if rec_type == _TYPE_RUNTIME_INFO:
                    yield _build_runtime_info(obj, trace_path, line_number)
                elif rec_type == _TYPE_INVOKE:
                    yield _build_invoke(obj, trace_path, line_number)
                else:
                    yield UnknownTraceRecord(type=rec_type, raw=obj)
                yielded_any = True

            if not yielded_any:
                raise NCPTraceSchemaError(
                    f"trace file {trace_path} is empty or contains no records; "
                    f"the runtime always emits a runtime_info record on success, "
                    f"so an empty trace indicates a trace-write failure"
                )
        except OSError as e:
            # File-system read error during enumerate() / f.readline().
            # NCPTraceSchemaError is a sibling of OSError, so an
            # in-flight NCPTraceSchemaError from the parse body is NOT
            # caught here and propagates unchanged.
            raise NCPTraceSchemaError(
                f"error reading trace file {trace_path} ({type(e).__name__}: {e})"
            ) from e
    finally:
        f.close()
