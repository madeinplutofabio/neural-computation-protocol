# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Binary discovery (§3.1, §10) and runtime version compatibility (§4.2).

The two public functions:

- `resolve_binary(user_override)` walks the §3.1 precedence
  (user kwarg, bundled wheel binary, PATH) and returns a Path,
  or raises NCPBinaryNotFoundError with the §10 message policy.
- `check_runtime_version(binary_path)` runs `<binary> --version`,
  concurrently drains stdout via a bounded reader thread (prevents
  pipe-buffer deadlock on verbose binaries), parses the clap-
  provided `ncp <version>` output, matches against
  SUPPORTED_RUNTIME_VERSIONS, returns the parsed version string.
  Raises NCPRuntimeVersionError on mismatch, malformed output,
  output overflow, reader I/O failure, or an unfinished reader
  thread; NCPTimeoutError on probe timeout; NCPBinaryNotFoundError
  on OSError at spawn.

Caching is the caller's responsibility: `runner.py` pins the resolved
path and version on each NCP instance; §10.
"""

from __future__ import annotations

import importlib.resources
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import IO, NamedTuple

from ncp_runtime._version import SUPPORTED_RUNTIME_VERSIONS
from ncp_runtime.types import (
    NCPBinaryNotFoundError,
    NCPRuntimeVersionError,
    NCPTimeoutError,
)

# Binary filename differs by platform: Windows uses .exe suffix.
_BINARY_NAME = "ncp.exe" if sys.platform == "win32" else "ncp"

# Marker file placed in _bundled/ by the Python-only fallback wheel
# build. discovery.py reads this to distinguish "Python-only
# installation (no bundled runtime)" from a generic lookup failure
# per §10.
_NO_BINARY_MARKER = ".no_binary"

# Version-probe hardening. The expected output is ~20 bytes
# (`ncp 0.3.6\n`); 4 KiB is ample headroom and tightly bounds the
# damage a misbehaving binary can do between spawn and timeout.
_VERSION_PROBE_CAP = 4096
_VERSION_PROBE_TIMEOUT = 5.0


class _ProbeOutput(NamedTuple):
    """Reader-thread result for the version probe.

    `data` is the captured prefix (bounded at `_VERSION_PROBE_CAP`).
    `overflowed` is True iff the stream produced more than the cap.
    `read_failed` is True iff the reader hit an OSError on stdout
    before EOF; the capture is untrusted in that case regardless of
    its content.
    """

    data: bytes
    overflowed: bool
    read_failed: bool


def _resolve_bundled_binary() -> Path | None:
    """Return the bundled binary Path if present, else None.

    Outcomes:
      * `_bundled/ncp[.exe]` exists and is a file: return its Path.
      * `_bundled/.no_binary` marker present (Python-only fallback
        wheel): return None.
      * Neither present: return None.

    The caller distinguishes Python-only-install from generic-missing
    by also checking `_is_python_only_install()`.
    """
    try:
        pkg = importlib.resources.files("ncp_runtime")
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    try:
        # Chain joinpath calls one argument at a time: Python 3.10's
        # Traversable.joinpath takes a single child (multi-arg
        # overload arrived in 3.11), and the SDK supports >=3.10.
        candidate = pkg.joinpath("_bundled").joinpath(_BINARY_NAME)
        if not candidate.is_file():
            return None
    except OSError:
        return None
    # Traversable to real filesystem Path. For a normally-installed
    # (extracted) wheel this is unambiguous; the second is_file()
    # catches the pathological case where Traversable.is_file()
    # returned True but str() did not give a real filesystem path.
    path = Path(str(candidate))
    if not path.is_file():
        return None
    return path.resolve()


def _is_python_only_install() -> bool:
    """True iff the installed wheel is the `py3-none-any` fallback.

    Detected by the presence of `_bundled/.no_binary` marker file
    (shipped only by the fallback wheel per §10, §11).
    """
    try:
        pkg = importlib.resources.files("ncp_runtime")
        # Chained joinpath for 3.10 Traversable compat (see
        # _resolve_bundled_binary comment).
        marker = pkg.joinpath("_bundled").joinpath(_NO_BINARY_MARKER)
        return bool(marker.is_file())
    except (ModuleNotFoundError, FileNotFoundError, OSError):
        return False


def _not_found_error(*, python_only: bool) -> NCPBinaryNotFoundError:
    """Construct NCPBinaryNotFoundError with §10-compliant message.

    Distinguishes Python-only vs generic lookup failure.
    """
    if python_only:
        header = (
            "Could not resolve the `ncp` runtime binary. "
            "This is a Python-only installation of ncp-runtime "
            "(no bundled runtime for your platform)."
        )
    else:
        header = "Could not resolve the `ncp` runtime binary."

    remediation = (
        "Discovery tried (in order): (1) the `binary` kwarg to NCP(), "
        "(2) the bundled wheel binary at "
        "importlib.resources.files('ncp_runtime') / '_bundled' / "
        f"'{_BINARY_NAME}', (3) shutil.which('ncp') on PATH. "
        "To proceed, install the runtime and either keep it on PATH "
        "or pass its absolute path to NCP(binary=...). Options:\n"
        "  * `cargo install ncp-runtime --locked`\n"
        "  * Download a prebuilt binary from "
        "https://github.com/madeinplutofabio/neural-computation-protocol/releases\n"
        '  * NCP(binary="/absolute/path/to/ncp")'
    )
    return NCPBinaryNotFoundError(f"{header}\n{remediation}")


def resolve_binary(user_override: str | Path | None) -> Path:
    """Resolve the ncp binary via the §3.1 precedence.

    Returns an absolute Path. Raises NCPBinaryNotFoundError if the
    precedence is exhausted.

    Precedence:
      1. user_override: if given, must point to an existing file.
      2. Bundled wheel binary at `_bundled/ncp[.exe]`.
      3. `shutil.which("ncp")` on PATH.
    """
    if user_override is not None:
        path = Path(user_override)
        if not path.is_file():
            raise NCPBinaryNotFoundError(
                f"NCP(binary=...) was given {str(path)!r}, but no file exists at that path."
            )
        return path.resolve()

    bundled = _resolve_bundled_binary()
    if bundled is not None:
        return bundled

    on_path = shutil.which("ncp")
    if on_path is not None:
        return Path(on_path).resolve()

    raise _not_found_error(python_only=_is_python_only_install())


def _drain_bounded(stream: IO[bytes], cap: int) -> _ProbeOutput:
    """Read `stream` until EOF, keeping at most `cap` bytes.

    Returns `_ProbeOutput(data, overflowed, read_failed)`:
      * `data`: at most `cap` captured bytes.
      * `overflowed`: True iff the stream produced more than `cap`
        total bytes. Excess bytes are drained to a bit-bucket so
        the subprocess cannot block on a full pipe.
      * `read_failed`: True iff an OSError occurred during read.
        The retained prefix is untrusted when this flag is set.
    """
    buf = bytearray()
    overflowed = False
    read_failed = False
    while True:
        try:
            chunk = stream.read(4096)
        except OSError:
            read_failed = True
            break
        if not chunk:
            break
        remaining = cap - len(buf)
        if remaining > 0:
            buf.extend(chunk[:remaining])
            if len(chunk) > remaining:
                overflowed = True
        else:
            overflowed = True
    return _ProbeOutput(data=bytes(buf), overflowed=overflowed, read_failed=read_failed)


def check_runtime_version(binary_path: Path) -> str:
    """Query `<binary_path> --version`, parse, verify.

    Spawn, concurrent-drain, wait, parse, match.

    Returns the parsed version string on success. Raises:
      * NCPBinaryNotFoundError if the subprocess cannot be started
        (OSError), since that indicates the resolved path points to
        something that is not an executable file.
      * NCPTimeoutError if the probe exceeds `_VERSION_PROBE_TIMEOUT`.
      * NCPRuntimeVersionError if the reader thread did not finish
        (captured output is untrusted); if the reader hit an I/O
        failure while reading stdout (captured output is untrusted);
        if the subprocess wrote more than `_VERSION_PROBE_CAP` bytes
        to stdout (overflow); if the exit code is nonzero; if the
        output cannot be parsed as exactly `ncp <version>` (two
        whitespace-separated tokens); or if the parsed version is
        not in SUPPORTED_RUNTIME_VERSIONS.

    Hardening:
      * A daemon reader thread drains stdout concurrently with the
        main-thread `proc.wait(timeout=...)`, so a verbose binary
        that writes more than one OS pipe buffer (~64 KiB on Linux)
        cannot deadlock itself against our wait and surface as a
        false timeout.
      * The reader keeps at most `_VERSION_PROBE_CAP` bytes; the
        rest is drained to a bit-bucket AND flagged as overflow.
        Overflow is a terminal failure: a binary that writes more
        than the cap is not trusted even if its retained prefix
        looks like `ncp 0.3.6`.
      * After the subprocess exits, the reader thread is joined
        and `is_alive()` checked before captured output is read.
        Reader I/O failures are tracked separately and rejected
        even if the retained prefix parses.
      * Error messages never echo stdout verbatim; the unsupported-
        version message bounds and `repr`-escapes the parsed
        version string before including it.
    """
    try:
        proc = subprocess.Popen(
            [str(binary_path), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as e:
        raise NCPBinaryNotFoundError(
            f"Could not execute resolved binary {binary_path} "
            f"to check its version ({type(e).__name__}: {e})."
        ) from e

    assert proc.stdout is not None
    stdout_pipe = proc.stdout
    captured: list[_ProbeOutput] = [
        _ProbeOutput(data=b"", overflowed=False, read_failed=False),
    ]

    def reader() -> None:
        captured[0] = _drain_bounded(stdout_pipe, _VERSION_PROBE_CAP)

    t = threading.Thread(
        target=reader,
        name="ncp-version-probe-reader",
        daemon=True,
    )
    t.start()

    try:
        try:
            proc.wait(timeout=_VERSION_PROBE_TIMEOUT)
        except subprocess.TimeoutExpired as e:
            proc.kill()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            t.join(timeout=1)  # best-effort; daemon won't block exit
            raise NCPTimeoutError(
                f"Version probe `{binary_path} --version` "
                f"exceeded {_VERSION_PROBE_TIMEOUT}s timeout."
            ) from e
        # Normal exit path: reader should finish as stdout EOFs.
        t.join(timeout=1)
        if t.is_alive():
            raise NCPRuntimeVersionError(
                f"Version probe reader thread for {binary_path} did "
                f"not finish after `--version` subprocess exited; "
                f"captured output cannot be trusted."
            )
    finally:
        try:
            stdout_pipe.close()
        except OSError:
            pass

    probe = captured[0]

    if probe.read_failed:
        raise NCPRuntimeVersionError(
            f"Reader thread for `{binary_path} --version` encountered "
            f"an I/O error while draining stdout; captured output "
            f"cannot be trusted."
        )

    if probe.overflowed:
        raise NCPRuntimeVersionError(
            f"Resolved binary {binary_path} wrote more than "
            f"{_VERSION_PROBE_CAP} bytes to `--version` stdout. "
            f"A valid `ncp <version>` response is ~20 bytes; "
            f"treating this as unexpected / untrusted output "
            f"regardless of what the retained prefix looks like."
        )

    if proc.returncode != 0:
        raise NCPRuntimeVersionError(
            f"Resolved binary {binary_path} exited with code "
            f"{proc.returncode} on `--version`. "
            f"Expected `ncp <version>` on stdout."
        )

    text = probe.data.decode("utf-8", errors="replace").strip()
    parts = text.split()
    if len(parts) != 2 or parts[0] != "ncp":
        # Do NOT echo stdout verbatim: it could contain binary
        # garbage, control characters, or sensitive strings from a
        # hostile or misconfigured binary.
        raise NCPRuntimeVersionError(
            f"Resolved binary {binary_path} returned unexpected "
            f"`--version` output (first {_VERSION_PROBE_CAP} bytes "
            f"did not parse as exactly `ncp <version>`). "
            f"Expected clap-format output `ncp <version>` with "
            f"exactly two whitespace-separated tokens."
        )
    version = parts[1]

    if version not in SUPPORTED_RUNTIME_VERSIONS:
        # Bound and repr-escape the version string before echoing;
        # a hostile binary could emit control characters or a long
        # string here.
        safe_version = repr(version[:64])
        raise NCPRuntimeVersionError(
            f"Resolved binary {binary_path} reports version "
            f"{safe_version}, which is not in this SDK's supported "
            f"set {list(SUPPORTED_RUNTIME_VERSIONS)!r}. Install a "
            f"compatible runtime or update ncp-runtime (Python SDK) "
            f"to a release that supports this runtime version."
        )

    return version
