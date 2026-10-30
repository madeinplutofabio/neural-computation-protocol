# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Unit tests for :mod:`ncp_runtime.discovery`.

Three sections:

1. ``resolve_binary`` -- binary-discovery precedence walk (§3.1).
   Monkeypatches ``_resolve_bundled_binary``,
   ``_is_python_only_install``, and ``shutil.which`` to drive
   each precedence branch without a real binary.
2. ``check_runtime_version`` -- subprocess version probe (§4.2).
   Monkeypatches ``subprocess.Popen`` with a ``_FakePopen`` that
   controls stdout bytes, exit code, and wait-timeout behavior.
   Exercises each failure branch (unexpected output, wrong
   version, nonzero exit, output overflow, reader-thread I/O
   failure, wall-clock timeout, spawn ``OSError``) without a
   real binary.
3. Integration -- happy paths against the real ``ncp`` binary
   via the ``ncp_binary`` fixture. Skipped when ``NCP_RUNTIME``
   is unset.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from typing import Any

import pytest

from ncp_runtime._version import SUPPORTED_RUNTIME_VERSIONS
from ncp_runtime.discovery import check_runtime_version, resolve_binary
from ncp_runtime.types import (
    NCPBinaryNotFoundError,
    NCPRuntimeVersionError,
    NCPTimeoutError,
)

# ======================================================================
# Section 1: resolve_binary -- precedence walk tests
# ======================================================================


def test_resolve_binary_user_override_valid(tmp_path: Path) -> None:
    """A user-supplied path that points to an existing file returns
    its resolved absolute Path.
    """
    fake = tmp_path / "ncp"
    fake.write_bytes(b"")
    assert resolve_binary(fake) == fake.resolve()


def test_resolve_binary_user_override_missing(tmp_path: Path) -> None:
    """A user-supplied path with no file at it raises
    ``NCPBinaryNotFoundError`` with an explicit "no file" message.
    """
    missing = tmp_path / "does-not-exist"
    with pytest.raises(NCPBinaryNotFoundError) as excinfo:
        resolve_binary(missing)
    assert "no file exists" in str(excinfo.value)


def test_resolve_binary_user_override_directory(tmp_path: Path) -> None:
    """A user-supplied path that is a directory (not a file) raises
    ``NCPBinaryNotFoundError`` -- precedence step 1 requires an
    existing FILE, not just any filesystem entry.
    """
    with pytest.raises(NCPBinaryNotFoundError):
        resolve_binary(tmp_path)


def test_resolve_binary_bundled_wins_over_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the bundled binary resolves, precedence step 2 returns
    it and ``shutil.which`` MUST NOT be consulted.

    The ``shutil.which`` mock fails if invoked, which proves the
    implementation never falls through to the PATH step when
    bundled is present (rather than merely verifying the final
    returned path happens to be bundled).
    """
    bundled = tmp_path / "bundled" / "ncp"
    bundled.parent.mkdir()
    bundled.write_bytes(b"")

    monkeypatch.setattr(
        "ncp_runtime.discovery._resolve_bundled_binary",
        lambda: bundled.resolve(),
    )

    def unexpected_which(name: str) -> None:
        pytest.fail("PATH must not be consulted when a bundled binary exists")

    monkeypatch.setattr("shutil.which", unexpected_which)

    assert resolve_binary(None) == bundled.resolve()


def test_resolve_binary_python_only_marker_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the bundled binary is absent AND the Python-only marker
    is present, the error message explicitly flags the install as
    Python-only per design doc §10.
    """
    monkeypatch.setattr("ncp_runtime.discovery._resolve_bundled_binary", lambda: None)
    monkeypatch.setattr("ncp_runtime.discovery._is_python_only_install", lambda: True)
    monkeypatch.setattr("shutil.which", lambda name: None)

    with pytest.raises(NCPBinaryNotFoundError) as excinfo:
        resolve_binary(None)
    assert "Python-only installation" in str(excinfo.value)


def test_resolve_binary_falls_back_to_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When no bundled binary and no marker, resolve walks to PATH
    via ``shutil.which`` and returns the resolved path.
    """
    on_path = tmp_path / "elsewhere" / "ncp"
    on_path.parent.mkdir()
    on_path.write_bytes(b"")

    monkeypatch.setattr("ncp_runtime.discovery._resolve_bundled_binary", lambda: None)
    monkeypatch.setattr("ncp_runtime.discovery._is_python_only_install", lambda: False)
    monkeypatch.setattr("shutil.which", lambda name: str(on_path))

    assert resolve_binary(None) == on_path.resolve()


def test_resolve_binary_nothing_found_generic_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When no precedence step matches and not a Python-only install,
    the error message is the generic form (no "Python-only" tag).
    """
    monkeypatch.setattr("ncp_runtime.discovery._resolve_bundled_binary", lambda: None)
    monkeypatch.setattr("ncp_runtime.discovery._is_python_only_install", lambda: False)
    monkeypatch.setattr("shutil.which", lambda name: None)

    with pytest.raises(NCPBinaryNotFoundError) as excinfo:
        resolve_binary(None)
    assert "Python-only installation" not in str(excinfo.value)
    assert "Could not resolve" in str(excinfo.value)


# ======================================================================
# Section 2: check_runtime_version -- mocked-subprocess tests
# ======================================================================


class _FakePopen:
    """Minimal mock of :class:`subprocess.Popen` for the version
    probe. Controls stdout bytes, exit code, and wait-timeout
    behavior per instance.

    :func:`check_runtime_version` spawns a daemon reader that reads
    ``self.stdout`` until EOF; :class:`io.BytesIO` EOFs cleanly on
    an empty remainder, so the reader thread finishes within
    microseconds of being started -- no race-sensitive timing.
    """

    def __init__(
        self,
        *,
        stdout_bytes: bytes = b"",
        exit_code: int = 0,
        wait_times_out: bool = False,
        stdout_stream: io.IOBase | None = None,
    ) -> None:
        self._exit_code = exit_code
        self._wait_times_out = wait_times_out
        self.stdout: Any = stdout_stream or io.BytesIO(stdout_bytes)
        self.returncode: int | None = None
        self.pid = 99999

    def wait(self, timeout: float | None = None) -> int:
        if self._wait_times_out:
            raise subprocess.TimeoutExpired(cmd="ncp", timeout=timeout)
        self.returncode = self._exit_code
        return self._exit_code

    def kill(self) -> None:
        return None

    def poll(self) -> int | None:
        return self.returncode


def _patch_popen(monkeypatch: pytest.MonkeyPatch, **popen_kwargs: Any) -> None:
    """Install a factory that returns a configured ``_FakePopen``
    for every ``subprocess.Popen`` call during the test.
    """

    def factory(argv: list[str], **_: Any) -> _FakePopen:
        return _FakePopen(**popen_kwargs)

    monkeypatch.setattr(subprocess, "Popen", factory)


def test_check_runtime_version_accepts_pinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``ncp 0.3.6\\n`` on stdout with exit 0 returns ``"0.3.6"``."""
    _patch_popen(monkeypatch, stdout_bytes=b"ncp 0.3.6\n", exit_code=0)
    assert check_runtime_version(tmp_path / "ncp") == "0.3.6"


def test_check_runtime_version_rejects_wrong_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parseable but unsupported version raises
    ``NCPRuntimeVersionError``. The message bounds + repr-escapes
    the parsed version (``'9.9.9'`` with quotes) and names the
    supported set.
    """
    _patch_popen(monkeypatch, stdout_bytes=b"ncp 9.9.9\n", exit_code=0)
    with pytest.raises(NCPRuntimeVersionError) as excinfo:
        check_runtime_version(tmp_path / "ncp")
    message = str(excinfo.value)
    assert "'9.9.9'" in message
    assert "0.3.6" in message


@pytest.mark.parametrize(
    "stdout_bytes,label",
    [
        (b"", "empty"),
        (b"ncp\n", "single-token"),
        (b"ncp 0.3.6 extra\n", "three-tokens"),
        (b"not-ncp 0.3.6\n", "wrong-first-token"),
        (b"garbage output here\n", "garbage"),
    ],
)
def test_check_runtime_version_rejects_unexpected_output(
    stdout_bytes: bytes,
    label: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Output that is not exactly ``ncp <version>`` (two
    whitespace-separated tokens with the first being ``ncp``) raises
    ``NCPRuntimeVersionError`` without echoing the raw stdout.
    """
    _patch_popen(monkeypatch, stdout_bytes=stdout_bytes, exit_code=0)
    with pytest.raises(NCPRuntimeVersionError) as excinfo:
        check_runtime_version(tmp_path / "ncp")
    message = str(excinfo.value)
    assert "garbage output here" not in message
    assert "exactly two whitespace-separated tokens" in message


def test_check_runtime_version_rejects_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nonzero exit on ``--version`` raises
    ``NCPRuntimeVersionError`` even when stdout parses cleanly --
    nonzero takes precedence over parse success.
    """
    _patch_popen(monkeypatch, stdout_bytes=b"ncp 0.3.6\n", exit_code=2)
    with pytest.raises(NCPRuntimeVersionError) as excinfo:
        check_runtime_version(tmp_path / "ncp")
    assert "exited with code 2" in str(excinfo.value)


def test_check_runtime_version_rejects_overflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A binary that writes more than ``_VERSION_PROBE_CAP`` (4096)
    bytes to ``--version`` stdout raises ``NCPRuntimeVersionError``
    EVEN if the retained prefix would parse cleanly. 5010 bytes =
    10 bytes of valid prefix + 5000 bytes of filler.
    """
    overflow_bytes = b"ncp 0.3.6\n" + b"x" * 5000
    _patch_popen(monkeypatch, stdout_bytes=overflow_bytes, exit_code=0)
    with pytest.raises(NCPRuntimeVersionError) as excinfo:
        check_runtime_version(tmp_path / "ncp")
    assert "wrote more than" in str(excinfo.value)


def test_check_runtime_version_rejects_read_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reader-thread I/O failure during stdout drain raises
    ``NCPRuntimeVersionError``; captured output is untrusted even
    if it would have parsed cleanly.
    """

    class _RaisingStream(io.RawIOBase):
        def read(self, size: int = -1) -> bytes:  # type: ignore[override]
            raise OSError("mock read failure")

        def readable(self) -> bool:
            return True

    _patch_popen(
        monkeypatch,
        stdout_bytes=b"",
        exit_code=0,
        stdout_stream=_RaisingStream(),
    )
    with pytest.raises(NCPRuntimeVersionError) as excinfo:
        check_runtime_version(tmp_path / "ncp")
    assert "I/O error" in str(excinfo.value)


def test_check_runtime_version_timeout_raises_timeout_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``proc.wait`` raising ``TimeoutExpired`` surfaces as
    :class:`NCPTimeoutError` with the probe timeout in the message.
    """
    _patch_popen(monkeypatch, wait_times_out=True)
    with pytest.raises(NCPTimeoutError) as excinfo:
        check_runtime_version(tmp_path / "ncp")
    assert "exceeded" in str(excinfo.value)


def test_check_runtime_version_spawn_oserror_raises_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A :class:`subprocess.Popen` that raises :class:`OSError` at
    spawn surfaces as :class:`NCPBinaryNotFoundError` with context
    about the inner error type and message.

    Uses errno 13 which Python auto-upcasts to
    :class:`PermissionError`. The implementation reports the
    SPECIFIC subclass name via ``type(e).__name__`` -- strictly
    more useful than hardcoding ``"OSError"``, which would hide
    the exact failure mode from a user reading the exception.
    """

    def failing_factory(argv: list[str], **_: Any) -> _FakePopen:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(subprocess, "Popen", failing_factory)
    with pytest.raises(NCPBinaryNotFoundError) as excinfo:
        check_runtime_version(tmp_path / "ncp")
    message = str(excinfo.value)
    assert "PermissionError" in message
    assert "Permission denied" in message


# ======================================================================
# Section 3: Integration -- happy paths against the real `ncp` binary
# ======================================================================


def test_resolve_binary_with_real_fixture(ncp_binary: Path) -> None:
    """Passing the real ``ncp`` binary as the user override resolves
    to its absolute path.
    """
    assert resolve_binary(ncp_binary) == ncp_binary.resolve()


def test_check_runtime_version_with_real_fixture_returns_pinned(
    ncp_binary: Path,
) -> None:
    """Running ``check_runtime_version`` against the real pinned
    binary returns ``"0.3.6"`` -- end-to-end confirmation that the
    Rust runtime's clap-formatted ``--version`` output passes the
    SDK's parse + supported-set gate.
    """
    assert check_runtime_version(ncp_binary) == "0.3.6"
    assert "0.3.6" in SUPPORTED_RUNTIME_VERSIONS
