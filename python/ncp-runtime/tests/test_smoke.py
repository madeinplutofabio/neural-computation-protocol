# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Smoke tests for ncp_runtime: imports, public API surface, and
exception hierarchy.

These tests do NOT require the ``ncp`` runtime binary; they
verify the SDK's structural contract that must hold at import
time regardless of whether integration is configured. Behavioral
tests live in :mod:`test_runner`, :mod:`test_discovery`,
:mod:`test_trace` and :mod:`test_trace_ownership`.
"""

from __future__ import annotations

import dataclasses
import inspect
import typing
from types import ModuleType


def test_import_ncp_runtime() -> None:
    """The top-level package imports without error."""
    import ncp_runtime

    assert ncp_runtime.__name__ == "ncp_runtime"


def test_version_string() -> None:
    """``__version__`` is a non-empty string."""
    import ncp_runtime

    assert isinstance(ncp_runtime.__version__, str)
    assert ncp_runtime.__version__ != ""


def test_supported_runtime_versions_shape() -> None:
    """``SUPPORTED_RUNTIME_VERSIONS`` is a non-empty tuple of
    non-empty strings. Per design doc §4.2 this is an exact-match
    tuple, not a semver range.
    """
    import ncp_runtime

    versions = ncp_runtime.SUPPORTED_RUNTIME_VERSIONS
    assert isinstance(versions, tuple)
    assert len(versions) > 0
    assert all(isinstance(v, str) and v for v in versions)


def test_supported_runtime_versions_exact_pin() -> None:
    """SDK v0.1.0 ships with an exact pin to runtime 0.3.6 per
    design doc §4.2.

    Tuple equality rather than ``in`` membership: a future
    expansion of the supported set (e.g., adding ``0.3.7``) must
    update this test deliberately, which forces the maintainer to
    re-read the version-compat contract and confirm the expansion
    is authorized. ``in`` membership would silently accept any
    superset.
    """
    import ncp_runtime

    assert ncp_runtime.SUPPORTED_RUNTIME_VERSIONS == ("0.3.6",)


def test_all_names_match_module_attributes() -> None:
    """Every name in ``__all__`` must resolve on the module."""
    import ncp_runtime

    missing = [name for name in ncp_runtime.__all__ if not hasattr(ncp_runtime, name)]
    assert not missing, f"__all__ lists names not present on the module: {missing}"


def test_no_stray_public_names() -> None:
    """No public, non-module attribute may exist outside ``__all__``.

    Catches accidental exposure of private helpers (e.g., someone
    adds a top-level ``foo = ...`` without listing it in
    ``__all__``). Submodules are allowed as attributes because
    Python's import system attaches them to the parent package.
    """
    import ncp_runtime

    strays = [
        name
        for name, value in vars(ncp_runtime).items()
        if not name.startswith("_")
        and not isinstance(value, ModuleType)
        and name not in ncp_runtime.__all__
    ]
    assert not strays, f"public attributes missing from __all__: {strays}"


def test_exception_hierarchy() -> None:
    """All SDK exceptions subclass ``NCPSDKError``, which subclasses
    ``Exception``. See design doc §6.
    """
    from ncp_runtime import (
        NCPBinaryNotFoundError,
        NCPRuntimeVersionError,
        NCPSDKError,
        NCPSubprocessError,
        NCPTimeoutError,
        NCPTraceSchemaError,
    )

    assert issubclass(NCPSDKError, Exception)
    for exc in (
        NCPBinaryNotFoundError,
        NCPRuntimeVersionError,
        NCPSubprocessError,
        NCPTimeoutError,
        NCPTraceSchemaError,
    ):
        assert issubclass(exc, NCPSDKError), f"{exc.__name__} is not an NCPSDKError"


def test_frozen_dataclasses() -> None:
    """``Terminal``, ``TerminalError``, and the trace record
    variants are frozen dataclasses. See design doc §3.2 (Terminal
    values) and §3.3 (trace record variants).

    Checks the ``__dataclass_params__.frozen`` flag rather than
    attempting a mutation + ``FrozenInstanceError``: constructing
    every record (particularly ``InvokeRecord`` with its 17 fields)
    is noise, and the dataclass decorator installs the
    frozen-enforcing ``__setattr__`` reliably off this flag.
    """
    from ncp_runtime import (
        InvokeRecord,
        RuntimeInfoRecord,
        Terminal,
        TerminalError,
        UnknownTraceRecord,
    )

    for cls in (
        Terminal,
        TerminalError,
        RuntimeInfoRecord,
        InvokeRecord,
        UnknownTraceRecord,
    ):
        assert dataclasses.is_dataclass(cls), f"{cls.__name__} is not a dataclass"
        assert cls.__dataclass_params__.frozen is True, f"{cls.__name__} is not frozen"


def test_trace_record_union_members() -> None:
    """``TraceRecord`` type alias enumerates exactly the three
    record variants. See design doc §3.3.
    """
    from ncp_runtime import (
        InvokeRecord,
        RuntimeInfoRecord,
        TraceRecord,
        UnknownTraceRecord,
    )

    members = set(typing.get_args(TraceRecord))
    assert members == {RuntimeInfoRecord, InvokeRecord, UnknownTraceRecord}


def test_ncp_and_result_are_classes() -> None:
    """``NCP`` and ``Result`` are importable as classes from the
    top level.
    """
    from ncp_runtime import NCP, Result

    assert inspect.isclass(NCP)
    assert inspect.isclass(Result)
