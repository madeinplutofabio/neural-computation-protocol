# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Unit tests for Result lifecycle + trace tempfile ownership (§5.1).

Focuses on:

  * Explicit ``.cleanup()`` semantics (idempotency,
    external-delete tolerance, FileNotFoundError silence).
  * Context manager cleanup on normal exit AND on body exception.
  * GC-based cleanup via ``weakref.finalize``.
  * Finalizer-detached-after-cleanup invariant: once
    ``.cleanup()`` succeeds, GC must NOT run the finalizer again
    (which could delete whatever file now lives at
    ``trace_path`` from a tempfile-name reuse).
  * ``.trace()`` iterator timing scenarios from §5.1:
      - ``.cleanup()`` then ``.trace()`` -> FileNotFoundError at
        call time (via the finalize-handle alive check).
      - ``.trace()`` then ``.cleanup()`` then ``next()`` ->
        FileNotFoundError at first consumption (via the
        ``cleanup_check`` callback). Single source of truth for
        both: the finalize-handle alive flag.
  * Multiple ``.trace()`` calls yield independent fresh iterators
    over the current file state.

Construction note: tests use ``Result()`` directly (not
``NCP.run()``) to isolate ownership-and-lifetime semantics from
subprocess spawning. The design doc §3.2 Result handle contract
is observable regardless of how ``trace_path`` originated.
"""

from __future__ import annotations

import gc
import json
from pathlib import Path

import pytest

from ncp_runtime import Result, RuntimeInfoRecord

_RUNTIME_INFO_LINE = (
    json.dumps(
        {
            "trace_schema_version": "ncp-trace-0.1",
            "type": "runtime_info",
            "runtime_version": "0.3.6",
            "wasmtime_version": "26.0.0",
            "timestamp": "2026-10-28T00:00:00Z",
        }
    )
    + "\n"
)


def _make_result_with_trace(tmp_path: Path, *, trace_content: str = "") -> tuple[Result, Path]:
    """Create a :class:`Result` bound to a real trace tempfile at
    ``tmp_path/trace.jsonl``. Returns the Result and the Path.

    The Result's weakref.finalize is attached by ``__init__``;
    callers who want to prove GC cleanup must drop their
    reference to the Result (the returned Path reference does
    NOT keep the Result alive).
    """
    trace_file = tmp_path / "trace.jsonl"
    trace_file.write_text(trace_content, encoding="utf-8")
    result = Result(
        terminals=(),
        result_type="Success",
        trace_path=trace_file,
    )
    return result, trace_file


# ======================================================================
# Section 1: Explicit cleanup
# ======================================================================


def test_cleanup_deletes_trace_file(tmp_path: Path) -> None:
    result, trace_file = _make_result_with_trace(tmp_path)
    assert trace_file.is_file()
    result.cleanup()
    assert not trace_file.exists()


def test_cleanup_idempotent_second_call(tmp_path: Path) -> None:
    """``.cleanup()`` silences FileNotFoundError so a second call
    is a no-op; the Result handle tolerates accidental
    double-cleanup without raising.
    """
    result, trace_file = _make_result_with_trace(tmp_path)
    result.cleanup()
    result.cleanup()
    assert not trace_file.exists()


def test_cleanup_tolerates_externally_deleted_file(tmp_path: Path) -> None:
    """If something else deletes the trace file first (user, a
    cleanup script, antivirus, etc.), ``.cleanup()`` is still a
    no-op rather than raising FileNotFoundError.
    """
    result, trace_file = _make_result_with_trace(tmp_path)
    trace_file.unlink()
    assert not trace_file.exists()
    result.cleanup()
    assert not trace_file.exists()


# ======================================================================
# Section 2: Context manager
# ======================================================================


def test_context_manager_deletes_on_normal_exit(tmp_path: Path) -> None:
    result, trace_file = _make_result_with_trace(tmp_path)
    with result as r:
        assert r.trace_path.is_file()
    assert not trace_file.exists()


def test_context_manager_deletes_on_body_exception(
    tmp_path: Path,
) -> None:
    """``__exit__`` runs cleanup even when the body raises."""

    class _TestException(Exception):
        pass

    result, trace_file = _make_result_with_trace(tmp_path)
    with pytest.raises(_TestException):
        with result:
            assert trace_file.is_file()
            raise _TestException("boom")
    assert not trace_file.exists()


# ======================================================================
# Section 3: GC-based cleanup via weakref.finalize
# ======================================================================


def _create_result_then_drop_reference(tmp_path: Path) -> Path:
    """Create a Result inside this function so it goes out of
    scope (and refcount drops to 0) on return. Caller gets only
    the Path, which does NOT keep the Result alive.
    """
    result, trace_file = _make_result_with_trace(tmp_path)
    assert trace_file.is_file()
    assert result is not None  # silence linters; local scope ends on return
    return trace_file


def test_gc_deletes_trace_file(tmp_path: Path) -> None:
    """When a Result goes out of scope with no explicit cleanup,
    the ``weakref.finalize`` callback deletes the trace file.

    On CPython the refcount hitting zero fires the finalizer
    synchronously; ``gc.collect()`` is defensive for any future
    runtime without deterministic refcount GC (e.g., PyPy, or a
    hypothetical cycle involving Result).
    """
    trace_file = _create_result_then_drop_reference(tmp_path)
    gc.collect()
    assert not trace_file.exists()


def test_finalizer_detached_after_cleanup(tmp_path: Path) -> None:
    """After explicit ``.cleanup()`` succeeds, the finalizer is
    detached. GC must NOT re-run it, which could otherwise delete
    whatever file now lives at ``trace_path`` -- a different
    process's file by sheer tempfile-name reuse would be a very
    bad outcome.

    We recreate a file at the same ``trace_path`` AFTER cleanup,
    then drop the Result and force GC. If the finalizer were
    still attached, it would delete our recreated file via
    ``_unlink_quiet``.
    """
    result, trace_file = _make_result_with_trace(tmp_path)
    result.cleanup()
    assert not trace_file.exists()

    trace_file.write_text("recreated after cleanup", encoding="utf-8")
    assert trace_file.is_file()

    del result
    gc.collect()

    assert trace_file.exists(), (
        "finalizer fired after cleanup already detached it; the recreated file was wrongly deleted"
    )


# ======================================================================
# Section 4: .trace() iterator timing (§5.1)
# ======================================================================


def test_trace_iterator_yields_runtime_info_record(
    tmp_path: Path,
) -> None:
    """Happy path: ``.trace()`` returns an iterator that yields
    the parsed records from the trace file.
    """
    result, _ = _make_result_with_trace(tmp_path, trace_content=_RUNTIME_INFO_LINE)
    try:
        records = list(result.trace())
        assert len(records) == 1
        assert isinstance(records[0], RuntimeInfoRecord)
        assert records[0].runtime_version == "0.3.6"
    finally:
        result.cleanup()


def test_trace_multiple_iterations_each_fresh(tmp_path: Path) -> None:
    """Each ``.trace()`` call returns an independent fresh
    iterator over the trace file; the iterators do not share
    state. Repeatable as long as the file hasn't been cleaned up.
    """
    result, _ = _make_result_with_trace(tmp_path, trace_content=_RUNTIME_INFO_LINE)
    try:
        records_1 = list(result.trace())
        records_2 = list(result.trace())
        assert len(records_1) == 1
        assert len(records_2) == 1
        assert records_1[0] == records_2[0]
    finally:
        result.cleanup()


def test_trace_after_cleanup_raises_file_not_found(
    tmp_path: Path,
) -> None:
    """§5.1 call-time check: ``.trace()`` AFTER ``.cleanup()``
    raises FileNotFoundError immediately via the finalize-handle
    alive check, before even attempting to open the trace file.
    """
    result, _ = _make_result_with_trace(tmp_path, trace_content=_RUNTIME_INFO_LINE)
    result.cleanup()
    with pytest.raises(FileNotFoundError):
        result.trace()


def test_trace_consumed_after_cleanup_raises_file_not_found(
    tmp_path: Path,
) -> None:
    """§5.1 consumption-time check: ``.trace()`` returns an
    iterator, THEN ``.cleanup()`` deletes the file, THEN first
    ``next()`` raises FileNotFoundError (NOT
    :class:`NCPTraceSchemaError`) via the ``cleanup_check``
    callback.

    Single source of truth for both the call-time and
    consumption-time scenarios: the finalize-handle alive flag.
    This test is the companion to
    :func:`test_trace_after_cleanup_raises_file_not_found` --
    both must raise the SAME exception type for the SAME
    post-cleanup contract violation.
    """
    result, _ = _make_result_with_trace(tmp_path, trace_content=_RUNTIME_INFO_LINE)
    iterator = result.trace()
    result.cleanup()
    with pytest.raises(FileNotFoundError):
        next(iterator)
