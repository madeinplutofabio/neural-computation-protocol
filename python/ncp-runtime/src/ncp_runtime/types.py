# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Public dataclasses, Result handle, and exception hierarchy.

Defines the SDK's frozen value types (Terminal, TerminalError), the
Result handle that owns the per-run trace file on disk, and the
NCPSDKError hierarchy. See design doc §3 (Public API) and §6
(Exception hierarchy).

Result.trace() is implemented in `trace.py` and imported lazily inside
the method to avoid a circular import between the two modules.
"""

from __future__ import annotations

import os
import weakref
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ncp_runtime.trace import TraceRecord


# --- Exceptions --------------------------------------------------------


class NCPSDKError(Exception):
    """Base class for all SDK-side failures.

    Graph-level outcomes (including `result_type == "Failure"`) are
    normal returns and do NOT raise. Only infrastructure problems
    raise. See design doc §6.
    """


class NCPBinaryNotFoundError(NCPSDKError):
    """Raised when the ncp binary cannot be resolved via the §3.1 precedence."""


class NCPRuntimeVersionError(NCPSDKError):
    """Raised when the resolved binary's version is not in SUPPORTED_RUNTIME_VERSIONS."""


class NCPTimeoutError(NCPSDKError):
    """Raised when a subprocess exceeds its wall-clock timeout."""


class NCPSubprocessError(NCPSDKError):
    """SDK-level subprocess failure. See design doc §6 and §4.4 rule 3.

    Attributes:
      category: one of "nonzero_exit", "malformed_stdout",
        "strict_validation_failed", "output_cap_exceeded".
      exit_code: process exit code, or None when the process did not
        exit cleanly before the cap/timeout branch.
      binary_path: resolved ncp binary.
      stderr: full captured stderr, bounded by §4.4 rule 8.

    The default __str__/.args carry only (category, exit_code,
    binary_path, stderr length). Full stderr lives on `.stderr` so
    callers who deliberately want it can read it. Setting the env var
    `NCP_RUNTIME_STDERR_IN_MESSAGE=1` flips the default and includes
    a stderr tail (last 2 KiB, UTF-8 replace-decoded) in the
    __str__/.args for debugging builds. Off in production.
    """

    category: str
    exit_code: int | None
    binary_path: Path
    stderr: bytes

    def __init__(
        self,
        category: str,
        exit_code: int | None,
        binary_path: Path,
        stderr: bytes,
    ) -> None:
        self.category = category
        self.exit_code = exit_code
        self.binary_path = binary_path
        self.stderr = stderr

        parts = [
            f"category={category}",
            f"exit_code={exit_code}",
            f"binary={binary_path}",
            f"stderr_len={len(stderr)}",
        ]
        if os.environ.get("NCP_RUNTIME_STDERR_IN_MESSAGE") == "1":
            tail = stderr[-2048:].decode("utf-8", errors="replace")
            parts.append(f"stderr_tail={tail!r}")
        super().__init__("; ".join(parts))


class NCPTraceSchemaError(NCPSDKError):
    """Trace schema mismatch, malformed known record, missing or
    unreadable trace file at .trace() time, or empty trace after a
    successful subprocess (design doc §8).
    """


# --- Terminal values ---------------------------------------------------


@dataclass(frozen=True)
class TerminalError:
    """One terminal's error payload. See design doc §4.1."""

    error_class: str
    message: str


@dataclass(frozen=True)
class Terminal:
    """One terminal result from a graph execution. Mirrors the CLI's
    --all-terminals element shape (§4.1).

    `output` is present iff the CLI emitted an `output` field;
    `error` is present iff the CLI emitted an `error` field.
    LowConfidence terminals can carry both.
    """

    node_id: str
    brick_id: str
    step: int
    result_type: str
    output: Any | None
    error: TerminalError | None


# --- Result handle -----------------------------------------------------


def _unlink_quiet(path: Path) -> None:
    """Unlink `path` if it exists; swallow OSError.

    GC-time finalizer callback. GC-time code must not raise, so this
    deliberately suppresses errors. Explicit `Result.cleanup()` uses
    a different path that propagates unexpected OSErrors so callers
    can see them (§3.2).
    """
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


class Result:
    """Terminal-result handle from NCP.run(). Owns the trace file.

    See design doc §3.2 for the full lifecycle rules.

    Common usage requires no cleanup understanding:

        result = ncp.run(graph, brick_dir, inputs)
        if result.result_type == "Success":
            print(result.terminals[0].output)

    For deterministic cleanup, use the context manager:

        with ncp.run(graph, brick_dir, inputs) as result:
            ...

    `weakref.finalize` is attached in `__init__` and deletes the trace
    file on GC as a safety net. After explicit cleanup/__exit__, the
    finalizer is detached. Runners MUST NOT attach a second finalizer
    externally; this constructor is the sole attachment site.
    """

    __slots__ = (
        "terminals",
        "result_type",
        "trace_path",
        "_finalize_handle",
        "__weakref__",
    )

    terminals: tuple[Terminal, ...]
    result_type: str
    trace_path: Path
    # Typeshed's generic signature for `weakref.finalize` has
    # changed across Python versions (arity 1 vs 2). Suppressing
    # the type-arg complaint here keeps the floor-of-supported-
    # range (3.10) and newer mypy runs both clean, without
    # pinning us to one stub shape.
    _finalize_handle: weakref.finalize  # type: ignore[type-arg]

    def __init__(
        self,
        terminals: tuple[Terminal, ...],
        result_type: str,
        trace_path: Path,
    ) -> None:
        self.terminals = terminals
        self.result_type = result_type
        self.trace_path = trace_path
        self._finalize_handle = weakref.finalize(self, _unlink_quiet, trace_path)

    def trace(self) -> Iterator[TraceRecord]:
        """Lazy iterator over the JSONL trace records. See §8.

        Call-time + consumption-time check against
        `self._finalize_handle.alive` ensures the post-cleanup
        contract violation (`FileNotFoundError`) fires correctly in
        both of these ordering scenarios:
          * `.cleanup(); .trace()`           -> raises here immediately.
          * `.trace(); .cleanup(); next()`   -> raises inside the
            iterator at first consumption via the cleanup_check
            callback.
        Single source of truth: the finalize handle's `alive` flag,
        observed at both moments.
        """
        if not self._finalize_handle.alive:
            raise FileNotFoundError(
                f"trace() called after cleanup; trace file no longer exists: {self.trace_path}"
            )
        # Lazy import avoids a circular dependency: trace.py imports
        # NCPTraceSchemaError from this module, and Result lives here.
        from ncp_runtime.trace import iter_trace_records

        handle = self._finalize_handle
        return iter_trace_records(
            self.trace_path,
            cleanup_check=lambda: handle.alive,
        )

    def cleanup(self) -> None:
        """Delete the trace file. Idempotent across already-deleted state.

        Explicit cleanup uses `Path.unlink()` directly (not the quiet
        helper). Behavior:

          * File deleted successfully: finalizer detached; done.
          * File already absent (FileNotFoundError): finalizer
            detached; done. Idempotent.
          * Any other OSError (e.g., Windows PermissionError when the
            file is still open by another process): propagates to the
            caller. The weakref finalizer stays attached as a safety
            net so GC or a later cleanup() call can retry.

        See design doc §3.2 for the policy. Making unexpected
        failures visible rather than silently leaking the temp file
        is deliberate.
        """
        try:
            self.trace_path.unlink()
        except FileNotFoundError:
            pass
        self._finalize_handle.detach()

    def __enter__(self) -> Result:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.cleanup()
