# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Unit tests for :mod:`ncp_runtime.trace`.

Covers the lazy JSONL iterator semantics (§8):

  * Trace schema version validation and the empty-trace rule
  * Known-record builders (RuntimeInfoRecord, InvokeRecord) with
    strict field-type enforcement and nullable semantics
  * Unknown-record forward-compat fallback (UnknownTraceRecord)
  * Error translation (OSError at open, invalid JSON, non-dict
    record, missing/non-string ``type`` field)
  * cleanup_check contract-violation callback (§5.1)
  * Silent skipping of blank/whitespace-only lines
  * Silent acceptance of extra fields on known record types
    (e.g., ``carry_state_hash`` currently emitted by the runtime
    but not in §3.3's ``InvokeRecord``)

These tests do NOT require the ``ncp`` runtime binary; the trace
parser operates purely on file contents.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ncp_runtime.trace import (
    InvokeRecord,
    RuntimeInfoRecord,
    UnknownTraceRecord,
    iter_trace_records,
)
from ncp_runtime.types import NCPTraceSchemaError

# ======================================================================
# Helpers
# ======================================================================

_SCHEMA_VERSION = "ncp-trace-0.1"


def _write_trace(path: Path, records: list[dict[str, Any]]) -> None:
    """Write records as JSONL to ``path`` (one JSON object per line
    plus a trailing newline). UTF-8 encoded.
    """
    path.write_text(
        "\n".join(json.dumps(r) for r in records) + "\n",
        encoding="utf-8",
    )


def _runtime_info_record() -> dict[str, Any]:
    return {
        "trace_schema_version": _SCHEMA_VERSION,
        "type": "runtime_info",
        "runtime_version": "0.3.6",
        "wasmtime_version": "26.0.0",
        "timestamp": "2026-10-28T00:00:00Z",
    }


def _invoke_record(**overrides: Any) -> dict[str, Any]:
    """Build a complete valid invoke record, with per-call
    overrides for specific fields.

    Overrides REPLACE existing keys and ADD new unknown keys,
    which the parser must silently ignore (forward-compat).
    """
    base: dict[str, Any] = {
        "trace_schema_version": _SCHEMA_VERSION,
        "type": "invoke",
        "timestamp": "2026-10-28T00:00:01Z",
        "trace_id": "trace-abc",
        "session_id": "session-xyz",
        "step": 0,
        "graph_id": "g1",
        "graph_version": "sha256:aa",
        "brick_id": "b1",
        "brick_version": "0.1.0",
        "bundle_digest": "sha256:bb",
        "node_id": "n1",
        "trigger": {"source_node_id": "__root__", "edge_id": "root"},
        "input_hash": "sha256:cc",
        "output_hash": "sha256:dd",
        "result_len": 42,
        "result_type": "Success",
        "latency_ms": 1.5,
        "error_class": None,
    }
    base.update(overrides)
    return base


# ======================================================================
# Section 1: file-level errors
# ======================================================================


def test_trace_file_missing(tmp_path: Path) -> None:
    """Missing file at open time surfaces as NCPTraceSchemaError
    (not FileNotFoundError), with the originating OSError chained.
    """
    with pytest.raises(NCPTraceSchemaError) as excinfo:
        list(iter_trace_records(tmp_path / "nope.jsonl"))
    assert "not readable" in str(excinfo.value)
    assert excinfo.value.__cause__ is not None


def test_trace_empty_file_raises(tmp_path: Path) -> None:
    """An empty trace raises NCPTraceSchemaError because the
    runtime always emits a runtime_info record on success (§8);
    an empty trace indicates a trace-write failure.
    """
    path = tmp_path / "trace.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(NCPTraceSchemaError) as excinfo:
        list(iter_trace_records(path))
    assert "empty or contains no records" in str(excinfo.value)


def test_trace_whitespace_only_file_raises(tmp_path: Path) -> None:
    """A file containing only blank/whitespace lines is treated as
    empty (every line is skipped, nothing yielded, raise).
    """
    path = tmp_path / "trace.jsonl"
    path.write_text("\n\n   \n\t\n", encoding="utf-8")
    with pytest.raises(NCPTraceSchemaError):
        list(iter_trace_records(path))


# ======================================================================
# Section 2: line-level errors
# ======================================================================


def test_trace_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    path.write_text("not json at all\n", encoding="utf-8")
    with pytest.raises(NCPTraceSchemaError) as excinfo:
        list(iter_trace_records(path))
    message = str(excinfo.value)
    assert "invalid JSON" in message
    assert "line 1" in message


def test_trace_non_dict_record(tmp_path: Path) -> None:
    """A line that parses as valid JSON but isn't an object
    (e.g., bare number, array) raises.
    """
    path = tmp_path / "trace.jsonl"
    path.write_text("42\n", encoding="utf-8")
    with pytest.raises(NCPTraceSchemaError) as excinfo:
        list(iter_trace_records(path))
    assert "not a JSON object" in str(excinfo.value)


def test_trace_schema_version_mismatch(tmp_path: Path) -> None:
    """A record with a wrong trace_schema_version raises
    NCPTraceSchemaError naming found vs expected.
    """
    path = tmp_path / "trace.jsonl"
    rec = _runtime_info_record()
    rec["trace_schema_version"] = "ncp-trace-99.0"
    _write_trace(path, [rec])
    with pytest.raises(NCPTraceSchemaError) as excinfo:
        list(iter_trace_records(path))
    assert "schema version mismatch" in str(excinfo.value)


def test_trace_missing_type_field(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    rec: dict[str, Any] = {"trace_schema_version": _SCHEMA_VERSION}
    _write_trace(path, [rec])
    with pytest.raises(NCPTraceSchemaError) as excinfo:
        list(iter_trace_records(path))
    assert "'type' field" in str(excinfo.value)


def test_trace_non_string_type_field(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    rec: dict[str, Any] = {"trace_schema_version": _SCHEMA_VERSION, "type": 123}
    _write_trace(path, [rec])
    with pytest.raises(NCPTraceSchemaError) as excinfo:
        list(iter_trace_records(path))
    assert "'type' field" in str(excinfo.value)


def test_trace_blank_lines_skipped(tmp_path: Path) -> None:
    """Blank or whitespace-only lines between records are silently
    skipped per §8.
    """
    path = tmp_path / "trace.jsonl"
    content = json.dumps(_runtime_info_record()) + "\n\n   \n" + json.dumps(_invoke_record()) + "\n"
    path.write_text(content, encoding="utf-8")
    records = list(iter_trace_records(path))
    assert len(records) == 2
    assert isinstance(records[0], RuntimeInfoRecord)
    assert isinstance(records[1], InvokeRecord)


# ======================================================================
# Section 3: RuntimeInfoRecord
# ======================================================================


def test_runtime_info_record_happy_path(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    _write_trace(path, [_runtime_info_record()])
    records = list(iter_trace_records(path))
    assert len(records) == 1
    rec = records[0]
    assert isinstance(rec, RuntimeInfoRecord)
    assert rec.trace_schema_version == _SCHEMA_VERSION
    assert rec.runtime_version == "0.3.6"
    assert rec.wasmtime_version == "26.0.0"
    assert rec.timestamp == "2026-10-28T00:00:00Z"


def test_runtime_info_missing_required_field(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    rec = _runtime_info_record()
    del rec["runtime_version"]
    _write_trace(path, [rec])
    with pytest.raises(NCPTraceSchemaError) as excinfo:
        list(iter_trace_records(path))
    assert "missing required field 'runtime_version'" in str(excinfo.value)


def test_runtime_info_wrong_field_type(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    rec = _runtime_info_record()
    rec["runtime_version"] = 42
    _write_trace(path, [rec])
    with pytest.raises(NCPTraceSchemaError) as excinfo:
        list(iter_trace_records(path))
    message = str(excinfo.value)
    assert "runtime_version" in message
    assert "wrong type" in message


# ======================================================================
# Section 4: InvokeRecord
# ======================================================================


def test_invoke_record_happy_path(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    _write_trace(path, [_invoke_record()])
    records = list(iter_trace_records(path))
    assert len(records) == 1
    rec = records[0]
    assert isinstance(rec, InvokeRecord)
    assert rec.step == 0
    assert rec.brick_id == "b1"
    assert rec.result_type == "Success"
    assert rec.latency_ms == 1.5
    assert rec.output_hash == "sha256:dd"
    assert rec.result_len == 42
    assert rec.error_class is None


def test_invoke_record_optional_fields_absent(tmp_path: Path) -> None:
    """Optional fields can be absent entirely, not just null."""
    path = tmp_path / "trace.jsonl"
    rec = _invoke_record()
    del rec["output_hash"]
    del rec["result_len"]
    del rec["error_class"]
    _write_trace(path, [rec])
    records = list(iter_trace_records(path))
    parsed = records[0]
    assert isinstance(parsed, InvokeRecord)
    assert parsed.output_hash is None
    assert parsed.result_len is None
    assert parsed.error_class is None


@pytest.mark.parametrize("field", ["output_hash", "result_len", "error_class"])
def test_invoke_record_optional_explicit_null(tmp_path: Path, field: str) -> None:
    """Each optional field accepts an explicit JSON ``null`` and
    yields ``None`` on the Python side.

    Distinct from the absent-key case tested above: the parser
    must treat both "key absent" and "key present with null value"
    identically, and both as distinct from "key present with wrong
    type" (tested further down).
    """
    path = tmp_path / "trace.jsonl"
    _write_trace(path, [_invoke_record(**{field: None})])
    parsed = list(iter_trace_records(path))[0]
    assert isinstance(parsed, InvokeRecord)
    assert getattr(parsed, field) is None


def test_invoke_record_latency_integer_normalized_to_float(
    tmp_path: Path,
) -> None:
    """``latency_ms`` emitted as a JSON integer (e.g., ``3``) is
    normalized to float on parse per §3.3 implementation note.
    """
    path = tmp_path / "trace.jsonl"
    _write_trace(path, [_invoke_record(latency_ms=3)])
    records = list(iter_trace_records(path))
    rec = records[0]
    assert isinstance(rec, InvokeRecord)
    assert rec.latency_ms == 3.0
    assert isinstance(rec.latency_ms, float)


def test_invoke_record_latency_bool_rejected(tmp_path: Path) -> None:
    """JSON ``true``/``false`` are not valid ``latency_ms`` values
    even though ``bool`` is a Python ``int`` subclass -- the parser
    explicitly rejects booleans for numeric fields.
    """
    path = tmp_path / "trace.jsonl"
    _write_trace(path, [_invoke_record(latency_ms=True)])
    with pytest.raises(NCPTraceSchemaError) as excinfo:
        list(iter_trace_records(path))
    message = str(excinfo.value)
    assert "latency_ms" in message
    assert "wrong type" in message


def test_invoke_record_step_bool_rejected(tmp_path: Path) -> None:
    """``step`` as JSON ``true`` cannot slip through as ``step=1``
    even though ``isinstance(True, int)`` returns True in Python.
    """
    path = tmp_path / "trace.jsonl"
    _write_trace(path, [_invoke_record(step=True)])
    with pytest.raises(NCPTraceSchemaError) as excinfo:
        list(iter_trace_records(path))
    assert "step" in str(excinfo.value)


def test_invoke_record_missing_required_field(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    rec = _invoke_record()
    del rec["brick_id"]
    _write_trace(path, [rec])
    with pytest.raises(NCPTraceSchemaError) as excinfo:
        list(iter_trace_records(path))
    assert "missing required field 'brick_id'" in str(excinfo.value)


def test_invoke_record_optional_wrong_type_rejected(
    tmp_path: Path,
) -> None:
    """Optional fields that are PRESENT but of a wrong type are
    rejected. Null and absence are both legitimate; wrong-type is
    not.
    """
    path = tmp_path / "trace.jsonl"
    _write_trace(path, [_invoke_record(output_hash=123)])
    with pytest.raises(NCPTraceSchemaError) as excinfo:
        list(iter_trace_records(path))
    assert "output_hash" in str(excinfo.value)


def test_invoke_record_extra_fields_ignored(tmp_path: Path) -> None:
    """Unknown EXTRA fields on a known record type (e.g.,
    ``carry_state_hash`` currently emitted by the Rust runtime but
    not in §3.3's InvokeRecord) are silently ignored per standard
    JSON "ignore unknown fields" discipline.
    """
    path = tmp_path / "trace.jsonl"
    _write_trace(
        path,
        [
            _invoke_record(
                carry_state_hash="sha256:deadbeef",
                some_future_field=99,
            )
        ],
    )
    records = list(iter_trace_records(path))
    rec = records[0]
    assert isinstance(rec, InvokeRecord)
    assert rec.brick_id == "b1"
    assert rec.result_type == "Success"


# ======================================================================
# Section 5: UnknownTraceRecord (forward compatibility)
# ======================================================================


def test_unknown_record_type_yields_unknown(tmp_path: Path) -> None:
    """A record with an unknown ``type`` within the accepted schema
    version yields ``UnknownTraceRecord(type, raw)`` rather than
    raising -- forward-compat per §8.
    """
    path = tmp_path / "trace.jsonl"
    rec: dict[str, Any] = {
        "trace_schema_version": _SCHEMA_VERSION,
        "type": "future_record_type",
        "arbitrary_data": [1, 2, 3],
    }
    _write_trace(path, [rec])
    records = list(iter_trace_records(path))
    parsed = records[0]
    assert isinstance(parsed, UnknownTraceRecord)
    assert parsed.type == "future_record_type"
    assert parsed.raw == rec


# ======================================================================
# Section 6: cleanup_check callback (§5.1)
# ======================================================================


def test_cleanup_check_returns_true_no_error(tmp_path: Path) -> None:
    """``cleanup_check`` returning True allows normal iteration."""
    path = tmp_path / "trace.jsonl"
    _write_trace(path, [_runtime_info_record()])
    records = list(iter_trace_records(path, cleanup_check=lambda: True))
    assert len(records) == 1


def test_cleanup_check_returns_false_raises_file_not_found(
    tmp_path: Path,
) -> None:
    """``cleanup_check`` returning False at first consumption raises
    :class:`FileNotFoundError` (NOT :class:`NCPTraceSchemaError`),
    so a caller who deferred consumption past ``.cleanup()`` sees
    the correct post-cleanup contract-violation signal rather than
    a misleading "trace schema error".
    """
    path = tmp_path / "trace.jsonl"
    _write_trace(path, [_runtime_info_record()])
    with pytest.raises(FileNotFoundError) as excinfo:
        list(iter_trace_records(path, cleanup_check=lambda: False))
    assert "after cleanup" in str(excinfo.value)


def test_cleanup_check_none_falls_through_to_os_error_path(
    tmp_path: Path,
) -> None:
    """``cleanup_check=None`` (the default) means no post-cleanup
    check runs; an absent file still goes through the OSError
    translation path and surfaces as NCPTraceSchemaError.
    """
    path = tmp_path / "does-not-exist.jsonl"
    with pytest.raises(NCPTraceSchemaError):
        list(iter_trace_records(path, cleanup_check=None))
