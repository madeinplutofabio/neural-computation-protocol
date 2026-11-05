# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Workflow installer, resolver, and side-by-side version manager.

Reuses D1's manifest / graph / semver validators so the installer and
the packager share one source of truth; the installer never instantiates
a second, incompatible validator. The Rust resolver is NOT cross-invoked
from this Python module. Rust retains execution authority at run time
(through NCP.run / NCP.run_workflow).

Supported archive sources: local filesystem path (including Windows
drive-letter and UNC paths, which are detected BEFORE URL parsing),
`file://` URL, `https://` URL. Everything else (http://, ssh://, git+*,
...) is rejected with a specific error.

Install flow (fail-fast; nothing lands at the target on failure):
  1. Fetch archive to a per-call tempfile under <home>/_staging/<uuid>/.
     HTTPS downloads are streamed with a size cap and redirect to any
     non-https scheme is rejected.
  2. Verify the archive's sha256 against the external --sha256 argument.
  3. Safely extract to the same staging dir (safety rules: no absolute
     paths, no `..`, no symlinks or hardlinks, no duplicates, no
     case-insensitive collisions, no Windows-reserved segments, no
     trailing '.' or ' ', no control characters; total extracted size
     capped at NCP_WORKFLOW_MAX_SIZE, default 100 MiB; exactly one
     top-level directory matching workflow.json name-version).
  4. Load + strict-validate workflow.json.
  5. Probe the resolved runtime; reject if its version does not satisfy
     workflow.runtime_compatibility.
  6. Full D1-level validation of graph + bricks (reusing pack._validate_brick).
  7. Write .install.json INTO the staged directory.
  8. Publish via mkdir-lock + marker + atomic per-file moves (see below).

Publishing — concurrent-install + crash-recovery design:

  The target slot `<home>/<name>/<version>/` is claimed via
  `os.mkdir(target)`, which fails EEXIST atomically on both POSIX and
  Windows. That single primitive IS the lock — no `target.exists()` +
  rename TOCTOU window remains, and there is no publish-staging sibling
  that a concurrent reaper could race with. On FileExistsError the
  installer classifies what it found at the target:
    * sentinel `.install.json` present AND no `.ncp-install.inprogress`
      marker -> immutable "already installed"
    * marker present, or target is empty -> incomplete install
      (either our prior crash or another process still populating);
      explicit recovery via `workflows remove`, then retry
    * neither -> foreign content; user must inspect and remove

  Once mkdir succeeds, the installer writes a `.ncp-install.inprogress`
  marker FIRST (recording pid + start time), then moves each staged
  child into target via `shutil.move`, which is `os.rename` on the same
  filesystem (same `<home>` tree). The marker is removed LAST, when the
  move is complete and `.install.json` is in place.

  Crash recovery:
    * Crash before marker write: target exists, empty -> "incomplete"
    * Crash during move: target has marker + partial content ->
      "incomplete". The marker stays, so `list_installed` hides it
      even if `.install.json` was already moved in.
    * Crash after marker removal: install is complete; nothing to do.

  `list_installed`, `show`, `resolve`, and `NCP.run_workflow` filter out
  any directory whose `.ncp-install.inprogress` is present or whose
  `.install.json` is absent, so no partial state surfaces as an
  installed workflow. `remove` reads the filesystem directly so it can
  clear incomplete-install zombies.

The resolver returns (graph_path, brick_dir) for the LangGraph consumer
path (D3). NCP.run_workflow() on the NCP class is wired in D3.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from ncp_runtime import discovery
from ncp_runtime._version import __version__ as _sdk_version
from ncp_runtime.types import (
    NCPSDKError,
    NCPWorkflowAmbiguousVersionError,
    NCPWorkflowDigestError,
    NCPWorkflowError,
    NCPWorkflowNotInstalledError,
    NCPWorkflowRuntimeIncompatibleError,
)
from ncp_runtime.workflows.pack import (
    _NAME_RE,
    _VERSION_RE,
    _check_constraint,
    _check_short_name_collisions,
    _collect_brick_refs,
    _load_yaml,
    _sha256_file,
    _validate_brick,
)

INSTALL_SCHEMA_VERSION = "ncp-install-0.1"
_WORKFLOW_SCHEMA = "ncp-workflow-0.1"
_WORKFLOW_JSON_KEYS = frozenset(
    {
        "schema_version",
        "name",
        "version",
        "description",
        "runtime_compatibility",
        "graph",
        "bricks_dir",
    }
)
_DEFAULT_MAX_SIZE = 100 * 1024 * 1024  # 100 MiB
_DEFAULT_MAX_ENTRIES = 10_000
_STAGING_REAP_AGE_S = 24 * 3600  # 24 hours
_HTTPS_TIMEOUT = 60.0
_DOWNLOAD_CHUNK = 65536
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_IN_PROGRESS_MARKER = ".ncp-install.inprogress"
_SENTINEL = ".install.json"
_STAGING_LOCK = ".lock"
# Names an archive MUST NOT contain under its root. These are installer-
# owned sentinels; an archive carrying one would either collide with our
# own write or be silently overwritten.
_RESERVED_ARCHIVE_NAMES: frozenset[str] = frozenset({_SENTINEL, _IN_PROGRESS_MARKER})
# Characters invalid in Windows filenames; rejecting them in archive
# segments keeps archives portable across platforms. `/` and `\` are
# path separators and handled earlier.
_WIN_INVALID_CHARS = '<>:"|?*'
# Only match multi-char schemes followed by `://`. Single-char "schemes"
# (Windows drive letters like `C:`) do not match, and schemes using the
# shorthand `scheme:` without `//` also do not match (we treat those as
# local paths, which is defensive).
_URL_RE = re.compile(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]+)://")

# Windows-reserved device names (case-insensitive, with or without extension).
_WIN_RESERVED: frozenset[str] = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
)


# -- Home + layout --------------------------------------------------------


def workflow_home() -> Path:
    """Resolve NCP_WORKFLOW_HOME (env override) or ~/.ncp/workflows/."""
    env = os.environ.get("NCP_WORKFLOW_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".ncp" / "workflows").resolve()


def _max_archive_bytes() -> int:
    env = os.environ.get("NCP_WORKFLOW_MAX_SIZE")
    if env is None:
        return _DEFAULT_MAX_SIZE
    try:
        v = int(env)
    except ValueError as e:
        raise NCPWorkflowError(
            f"invalid NCP_WORKFLOW_MAX_SIZE {env!r}; expected positive integer"
        ) from e
    if v <= 0:
        raise NCPWorkflowError(f"invalid NCP_WORKFLOW_MAX_SIZE {env!r}; must be > 0")
    return v


def _max_archive_entries() -> int:
    env = os.environ.get("NCP_WORKFLOW_MAX_ENTRIES")
    if env is None:
        return _DEFAULT_MAX_ENTRIES
    try:
        v = int(env)
    except ValueError as e:
        raise NCPWorkflowError(
            f"invalid NCP_WORKFLOW_MAX_ENTRIES {env!r}; expected positive integer"
        ) from e
    if v <= 0:
        raise NCPWorkflowError(f"invalid NCP_WORKFLOW_MAX_ENTRIES {env!r}; must be > 0")
    return v


def _reap_stale_staging_dirs() -> None:
    """Conservative cleanup of `<home>/_staging/*` directories.

    Policy: a staging sibling is reaped ONLY when all three conditions
    hold:
      (1) its mtime is older than `_STAGING_REAP_AGE_S`;
      (2) it contains a `.lock` file (explicit ownership evidence from
          the installer that created it);
      (3) that `.lock` file names a PID that is currently dead on this
          host.

    Any sibling that fails ANY condition — fresh enough, no lock file,
    unreadable/malformed lock, or lock PID still alive — is LEFT ALONE.
    A sibling without a `.lock` file is ambiguous; it could be from an
    installer that crashed before writing one, or from an unrelated
    tool using the same directory. The independent audit flagged that
    "a PID alone is not definitive cross-process ownership evidence",
    so we fail-safe: ambiguous → leave for manual cleanup.
    """
    import time as _time

    staging = workflow_home() / "_staging"
    if not staging.is_dir():
        return
    cutoff = _time.time() - _STAGING_REAP_AGE_S
    try:
        children = list(staging.iterdir())
    except OSError:
        return
    for p in children:
        if not p.is_dir():
            continue
        try:
            if p.stat().st_mtime >= cutoff:
                continue
            lock = p / _STAGING_LOCK
            if not lock.is_file():
                # No ownership evidence — never reap. Manual cleanup only.
                continue
            try:
                pid = int(lock.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                # Unreadable or malformed lock — treat as ambiguous.
                continue
            if _pid_alive(pid):
                continue
            # (1) stale, (2) lock present, (3) owner dead — reap.
            shutil.rmtree(p, ignore_errors=True)
        except OSError:
            continue


def _assert_name_safe(name: Any) -> None:
    """Validate a workflow name at every public entry point.

    Centralised here so install / resolve / show / remove / run_workflow
    all enforce the same `_NAME_RE` grammar BEFORE any filesystem path
    is constructed from `name`. A name containing `..` or a path
    separator would otherwise let a caller reach outside
    NCP_WORKFLOW_HOME (confirmed exploit against the previous
    `remove("../unrelated", all=True)` path).
    """
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise NCPWorkflowError(f"invalid workflow name {name!r}; must match {_NAME_RE.pattern}")


def _assert_version_safe(version: Any) -> None:
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        raise NCPWorkflowError(
            f"invalid workflow version {version!r}; must match {_VERSION_RE.pattern}"
        )


def _name_dir(name: str) -> Path:
    """Return <home>/<name>/ for a VALIDATED name.

    Additionally enforces path confinement: the resolved absolute path
    must be a direct child of <home>. This is defense-in-depth against
    any future grammar change in `_NAME_RE` that might inadvertently
    admit a traversal sequence.
    """
    _assert_name_safe(name)
    home = workflow_home()
    candidate = (home / name).resolve(strict=False)
    try:
        parent_rel = candidate.parent.resolve(strict=False)
    except OSError as e:
        raise NCPWorkflowError(f"cannot resolve workflow home: {e}") from e
    if parent_rel != home:
        raise NCPWorkflowError(
            f"workflow name {name!r} resolves outside workflow home "
            f"{home} (resolved to {candidate})"
        )
    return candidate


def _install_root(name: str, version: str) -> Path:
    _assert_version_safe(version)
    nd = _name_dir(name)
    candidate = (nd / version).resolve(strict=False)
    if candidate.parent.resolve(strict=False) != nd:
        raise NCPWorkflowError(
            f"workflow version {version!r} resolves outside name directory "
            f"{nd} (resolved to {candidate})"
        )
    return candidate


def _staging_root() -> Path:
    s = workflow_home() / "_staging"
    s.mkdir(parents=True, exist_ok=True)
    return s


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness check for a process id on this host."""
    if pid <= 0:
        return False
    try:
        # NOTE: use `sys.platform == "win32"` (not `os.name == "nt"`) so
        # mypy's platform narrowing sees this branch as Windows-only and
        # does not type-check `ctypes.windll` on Linux/macOS, where
        # `ctypes` has no `windll` attribute.
        if sys.platform == "win32":
            # On Windows, os.kill(pid, 0) raises on non-existent pids.
            # Use psutil-free probing via OpenProcess through ctypes.
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if handle == 0:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        os.kill(pid, 0)
        return True
    except (OSError, PermissionError):
        return False
    except Exception:
        return False


# -- Fetch ----------------------------------------------------------------


class _StrictHttpsRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects whose target scheme is not https."""

    def redirect_request(  # type: ignore[no-untyped-def]
        self, req, fp, code, msg, headers, newurl
    ):
        parsed = urllib.parse.urlparse(newurl)
        scheme = (parsed.scheme or "").lower()
        if scheme != "https":
            raise urllib.error.HTTPError(
                newurl,
                code,
                f"redirect to non-https scheme {scheme!r} rejected",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch_https(src: str, dest: Path, *, max_size: int) -> None:
    req = urllib.request.Request(src, headers={"User-Agent": "ncp-runtime"})
    opener = urllib.request.build_opener(_StrictHttpsRedirectHandler())
    try:
        with opener.open(req, timeout=_HTTPS_TIMEOUT) as resp:  # noqa: S310
            total = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(_DOWNLOAD_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_size:
                        raise NCPWorkflowError(
                            f"HTTPS download exceeds {max_size} bytes "
                            f"(NCP_WORKFLOW_MAX_SIZE); aborted"
                        )
                    f.write(chunk)
    except urllib.error.URLError as e:
        raise NCPWorkflowError(f"failed to fetch {src}: {e}") from e
    except OSError as e:
        raise NCPWorkflowError(f"failed to fetch {src}: {e}") from e


def _copy_local_bounded(src_path: Path, dest: Path, *, max_size: int) -> None:
    """Copy a local file to `dest`, rejecting if the on-disk size exceeds
    `max_size`. The pre-check closes the window where an oversized local
    archive would be copied in full before the extraction cap fires."""
    try:
        src_size = src_path.stat().st_size
    except OSError as e:
        raise NCPWorkflowError(f"cannot stat archive {src_path}: {e}") from e
    if src_size > max_size:
        raise NCPWorkflowError(
            f"archive at {src_path} is {src_size} bytes which exceeds "
            f"{max_size} bytes (NCP_WORKFLOW_MAX_SIZE); refusing to fetch"
        )
    shutil.copyfile(src_path, dest)


def _fetch_to(src: str, dest: Path, *, max_size: int) -> None:
    """Fetch src to dest. Native Windows paths (e.g. ``C:\\foo``) are detected
    BEFORE URL parsing so urlparse cannot misinterpret the drive letter as a
    scheme. URL schemes other than ``file://`` and ``https://`` are rejected.
    """
    m = _URL_RE.match(src)
    if not m:
        # Native filesystem path (POSIX absolute/relative, Windows
        # drive-letter / UNC, or relative path).
        src_path = Path(src).expanduser()
        if not src_path.is_file():
            raise NCPWorkflowError(f"archive not found: {src_path}")
        _copy_local_bounded(src_path, dest, max_size=max_size)
        return
    scheme = m.group("scheme").lower()
    if scheme == "file":
        parsed = urllib.parse.urlparse(src)
        # Reject file:// URLs with a non-local authority. urllib silently
        # discards the netloc, which on Windows would turn
        # 'file://fileserver/dist/x.tar.gz' into the wrong local path.
        # Supporting UNC paths cleanly is out of scope here; users can pass
        # a native UNC path (\\server\share\x.tar.gz) directly as `src`.
        netloc = (parsed.netloc or "").lower()
        if netloc not in ("", "localhost"):
            raise NCPWorkflowError(
                f"file:// URL has non-local authority {parsed.netloc!r}; "
                f"pass a local path or a UNC path (\\\\server\\share\\...) "
                f"as `src` instead of a file:// URL"
            )
        src_path = Path(urllib.request.url2pathname(parsed.path)).expanduser()
        if not src_path.is_file():
            raise NCPWorkflowError(f"archive not found: {src_path}")
        _copy_local_bounded(src_path, dest, max_size=max_size)
    elif scheme == "https":
        _fetch_https(src, dest, max_size=max_size)
    else:
        raise NCPWorkflowError(
            f"unsupported source scheme {scheme!r}: only local paths, "
            f"file://, and https:// are supported"
        )


# -- SHA ------------------------------------------------------------------


def _normalize_sha256(s: str) -> str:
    v = s.strip().lower()
    if v.startswith("sha256:"):
        v = v[len("sha256:") :]
    if not _SHA256_HEX_RE.match(v):
        raise NCPWorkflowError(
            f"invalid --sha256 argument {s!r}; expected 64 hex characters "
            f"(optionally prefixed by 'sha256:')"
        )
    return v


def _verify_external_sha(archive: Path, expected_arg: str) -> str:
    expected = _normalize_sha256(expected_arg)
    actual = _sha256_file(archive)
    if actual.lower() != expected:
        raise NCPWorkflowDigestError(f"archive sha256 mismatch: expected {expected}, got {actual}")
    return actual


# -- Safe extraction ------------------------------------------------------


def _is_abs_or_drive(name: str) -> bool:
    if name.startswith("/") or name.startswith("\\"):
        return True
    return bool(re.match(r"^[A-Za-z]:[\\/]?", name))


def _check_safe_segment(entry_name: str, segment: str) -> None:
    """Validate one path segment extracted from a tar entry name."""
    if not segment:
        raise NCPWorkflowError(f"archive entry {entry_name!r} has empty segment")
    # Archives cannot carry installer-owned sentinel filenames; those are
    # written by install() itself and must not come from the archive.
    if segment in _RESERVED_ARCHIVE_NAMES:
        raise NCPWorkflowError(
            f"archive entry {entry_name!r} contains installer-reserved "
            f"filename {segment!r}; refusing to extract"
        )
    # Reject characters that Windows disallows in filenames. `:` is
    # particularly dangerous because NTFS interprets `foo:bar` as an
    # alternate-data-stream reference. Keeping archives portable requires
    # rejecting these up-front rather than discovering the problem at
    # extraction time on Windows.
    bad = [c for c in _WIN_INVALID_CHARS if c in segment]
    if bad:
        raise NCPWorkflowError(
            f"archive entry {entry_name!r} segment {segment!r} contains "
            f"Windows-invalid character(s) {bad!r}; refusing to extract"
        )
    # Windows's reserved-name rule applies to the component BEFORE the first
    # dot, not the last: NUL.config.json also resolves to NUL. Split on the
    # FIRST dot.
    base = segment.split(".", 1)[0] if "." in segment else segment
    if base.lower() in _WIN_RESERVED:
        raise NCPWorkflowError(
            f"archive entry {entry_name!r} contains Windows-reserved segment "
            f"{segment!r}; refusing to extract"
        )
    if any(c == "\x00" or ord(c) < 32 for c in segment):
        raise NCPWorkflowError(
            f"archive entry {entry_name!r} contains control character in segment {segment!r}"
        )
    if segment.endswith(".") or segment.endswith(" "):
        raise NCPWorkflowError(
            f"archive entry {entry_name!r} segment {segment!r} ends with '.' "
            f"or ' '; unsafe on Windows"
        )


def _safe_extract_tgz(
    archive: Path, staging: Path, *, max_size: int, max_entries: int
) -> tuple[Path, str]:
    """Extract archive into staging. Returns (extracted_root, root_name)."""
    if not tarfile.is_tarfile(archive):
        raise NCPWorkflowError(f"not a valid tar archive: {archive}")

    staging.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    seen_lower: dict[str, str] = {}
    total = 0
    top_dirs: set[str] = set()

    try:
        with tarfile.open(archive, "r:gz") as tf:
            members = tf.getmembers()
            if not members:
                raise NCPWorkflowError("archive is empty")
            if len(members) > max_entries:
                raise NCPWorkflowError(
                    f"archive has {len(members)} entries which exceeds "
                    f"{max_entries} (NCP_WORKFLOW_MAX_ENTRIES); refusing to extract"
                )

            for m in members:
                if not m.name:
                    raise NCPWorkflowError("archive contains an empty-name entry")
                if not (m.isreg() or m.isdir()):
                    raise NCPWorkflowError(
                        f"archive contains non-regular entry {m.name!r} "
                        f"(type-flag {m.type!r}); only files and directories "
                        f"are allowed"
                    )
                if _is_abs_or_drive(m.name):
                    raise NCPWorkflowError(f"absolute path not allowed in archive: {m.name!r}")
                parts = re.split(r"[/\\]", m.name)
                if any(p == ".." for p in parts):
                    raise NCPWorkflowError(f"parent-traversal in archive entry: {m.name!r}")
                normalized = m.name.replace("\\", "/").rstrip("/")
                if not normalized:
                    raise NCPWorkflowError(f"archive entry {m.name!r} normalizes to empty")
                for seg in normalized.split("/"):
                    _check_safe_segment(m.name, seg)
                if normalized in seen:
                    raise NCPWorkflowError(f"duplicate archive entry: {m.name!r}")
                seen.add(normalized)
                key = normalized.lower()
                if key in seen_lower and seen_lower[key] != normalized:
                    raise NCPWorkflowError(
                        f"case-insensitive collision in archive: "
                        f"{seen_lower[key]!r} and {m.name!r} differ only in "
                        f"case; refusing to extract (unsafe on Windows and "
                        f"macOS default filesystems)"
                    )
                seen_lower[key] = normalized
                top_dirs.add(normalized.split("/", 1)[0])
                if m.isreg():
                    total += m.size
                    if total > max_size:
                        raise NCPWorkflowError(
                            f"archive extracted size exceeds {max_size} "
                            f"bytes (NCP_WORKFLOW_MAX_SIZE); refusing to extract"
                        )

            if len(top_dirs) != 1:
                raise NCPWorkflowError(
                    f"archive must contain exactly one top-level directory; "
                    f"found {sorted(top_dirs)}"
                )
            root_name = next(iter(top_dirs))

            for m in members:
                target = staging / m.name.replace("\\", "/")
                if m.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                src = tf.extractfile(m)
                if src is None:
                    raise NCPWorkflowError(f"cannot extract {m.name!r}")
                with open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
    except tarfile.TarError as e:
        raise NCPWorkflowError(f"malformed archive {archive}: {e}") from e

    extracted = staging / root_name
    if not extracted.is_dir():
        raise NCPWorkflowError(f"archive did not contain the declared root directory {root_name!r}")
    return extracted, root_name


# -- workflow.json validation ---------------------------------------------


def _load_workflow_json(root: Path) -> dict[str, Any]:
    wf_path = root / "workflow.json"
    if not wf_path.is_file():
        raise NCPWorkflowError(f"workflow.json missing in archive: {wf_path}")
    try:
        wf = json.loads(wf_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise NCPWorkflowError(f"workflow.json is not valid JSON: {e}") from e
    if not isinstance(wf, dict):
        raise NCPWorkflowError("workflow.json root is not an object")
    extra = set(wf) - _WORKFLOW_JSON_KEYS
    if extra:
        raise NCPWorkflowError(f"workflow.json has unknown keys: {sorted(extra)}")
    missing = _WORKFLOW_JSON_KEYS - set(wf)
    if missing:
        raise NCPWorkflowError(f"workflow.json missing required keys: {sorted(missing)}")
    if wf["schema_version"] != _WORKFLOW_SCHEMA:
        raise NCPWorkflowError(
            f"workflow.json schema_version {wf['schema_version']!r}, expected {_WORKFLOW_SCHEMA!r}"
        )
    if wf["graph"] != "graph.yaml":
        raise NCPWorkflowError(f"workflow.json 'graph' must be 'graph.yaml', got {wf['graph']!r}")
    if wf["bricks_dir"] != "bricks":
        raise NCPWorkflowError(
            f"workflow.json 'bricks_dir' must be 'bricks', got {wf['bricks_dir']!r}"
        )
    if not isinstance(wf["name"], str) or not _NAME_RE.match(wf["name"]):
        raise NCPWorkflowError(f"workflow.json invalid name: {wf['name']!r}")
    if not isinstance(wf["version"], str) or not _VERSION_RE.match(wf["version"]):
        raise NCPWorkflowError(f"workflow.json invalid version: {wf['version']!r}")
    if not isinstance(wf["runtime_compatibility"], str):
        raise NCPWorkflowError("workflow.json runtime_compatibility must be a string")
    if not isinstance(wf["description"], str):
        raise NCPWorkflowError("workflow.json description must be a string")
    return wf


# -- Runtime compat -------------------------------------------------------


def _check_runtime_compat(constraint: str, *, binary: str | Path | None = None) -> str:
    """Probe the resolved runtime; reject if its version violates `constraint`."""
    bin_path = discovery.resolve_binary(binary)
    runtime_version = discovery.check_runtime_version(bin_path)
    if not _check_constraint(constraint, runtime_version):
        raise NCPWorkflowRuntimeIncompatibleError(
            f"workflow requires runtime {constraint!r}, "
            f"resolved runtime {bin_path} reports version {runtime_version!r}"
        )
    return runtime_version


# -- Full tree validation (reuses D1 helpers) -----------------------------


def _validate_installed_tree(root: Path, wf: dict[str, Any]) -> list[dict[str, Any]]:
    graph_path = root / wf["graph"]
    brick_dir = root / wf["bricks_dir"]
    if not graph_path.is_file():
        raise NCPWorkflowError(f"graph file missing: {graph_path}")
    if not brick_dir.is_dir():
        raise NCPWorkflowError(f"bricks dir missing: {brick_dir}")
    graph_obj = _load_yaml(graph_path)
    refs = _collect_brick_refs(graph_obj, graph_path)
    _check_short_name_collisions(refs)
    validated: dict[str, dict[str, Any]] = {}
    for ref in refs:
        bid = ref["brick_id"]
        if bid not in validated:
            # _validate_brick raises NCPWorkflowDigestError directly for
            # integrity failures (digest/size mismatch); other errors keep
            # the base NCPWorkflowError type. No substring-match coupling.
            validated[bid] = _validate_brick(brick_dir, bid)
    for ref in refs:
        info = validated[ref["brick_id"]]
        if not _check_constraint(ref["version_or_range"], info["manifest_version"]):
            raise NCPWorkflowError(
                f"version mismatch: node {ref['node_id']!r} requires "
                f"{ref['brick_id']!r} {ref['version_or_range']!r}, but "
                f"manifest declares {info['manifest_version']!r}"
            )
    return [
        {"brick_id": bid, "digest": info["digest"], "size_bytes": info["size"]}
        for bid, info in validated.items()
    ]


# -- Public API: install --------------------------------------------------


def install(
    archive: str | Path,
    *,
    sha256: str,
    binary: str | Path | None = None,
) -> dict[str, Any]:
    """Install a workflow archive by (name, version).

    `archive` is a local filesystem path, `file://` URL, or `https://` URL.
    `sha256` is the external 64-hex-digit archive digest (required).
    `binary` optionally overrides the ncp runtime used for compatibility
    probing; when None, the SDK's standard discovery precedence applies.
    """
    src = str(archive)
    home = workflow_home()
    home.mkdir(parents=True, exist_ok=True)
    staging_parent = _staging_root()
    # Best-effort cleanup of stale _staging/* siblings from crashed
    # prior installs. Age-gated so in-flight concurrent installs are safe.
    _reap_stale_staging_dirs()
    max_size = _max_archive_bytes()
    max_entries = _max_archive_entries()

    with tempfile.TemporaryDirectory(dir=str(staging_parent)) as td:
        # Write a .lock file naming the current PID so the age-based
        # staging reaper never deletes a sibling owned by a live install
        # (even one that happens to be running > _STAGING_REAP_AGE_S).
        try:
            (Path(td) / _STAGING_LOCK).write_text(str(os.getpid()), encoding="utf-8")
        except OSError:
            # Best-effort; even without the lock, the age gate is still
            # a conservative window.
            pass

        tmp_archive = Path(td) / "download.tar.gz"
        _fetch_to(src, tmp_archive, max_size=max_size)
        actual_sha = _verify_external_sha(tmp_archive, sha256)

        stage = Path(td) / "stage"
        stage.mkdir()
        extracted_root, root_name = _safe_extract_tgz(
            tmp_archive, stage, max_size=max_size, max_entries=max_entries
        )

        wf = _load_workflow_json(extracted_root)
        name = str(wf["name"])
        version = str(wf["version"])
        canonical_root = f"{name}-{version}"
        if root_name != canonical_root:
            raise NCPWorkflowError(
                f"archive top-level directory {root_name!r} does not match "
                f"workflow.json name-version {canonical_root!r}"
            )

        runtime_version = _check_runtime_compat(wf["runtime_compatibility"], binary=binary)
        bricks = _validate_installed_tree(extracted_root, wf)

        install_meta = {
            "schema_version": INSTALL_SCHEMA_VERSION,
            "name": name,
            "version": version,
            "description": wf["description"],
            "source": src,
            "archive_sha256": actual_sha,
            "runtime_compatibility": wf["runtime_compatibility"],
            "runtime_version_at_install": runtime_version,
            "sdk_version": _sdk_version,
            "installed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        }
        # Write the install-metadata sentinel INSIDE staging so it moves
        # into `target` as part of the normal file-move loop below. The
        # sentinel being present + the in-progress marker being absent is
        # how `list_installed` / `resolve` / `show` / `run_workflow` decide
        # a slot is a complete install.
        staged_meta = extracted_root / _SENTINEL
        staged_meta.write_text(json.dumps(install_meta, indent=2) + "\n", encoding="utf-8")

        target = _install_root(name, version)
        target.parent.mkdir(parents=True, exist_ok=True)

        # Publish — mkdir-lock + marker. `os.mkdir(target)` is the atomic
        # claim: POSIX mkdir fails EEXIST, Windows CreateDirectoryW fails
        # ERROR_ALREADY_EXISTS. There is no TOCTOU between a preflight
        # `target.exists()` check and the publish operation — the mkdir
        # IS the check. An existing empty target created by a foreign
        # process is NOT overwritten; `os.mkdir` refuses it the same way
        # it refuses a populated one.
        try:
            os.mkdir(target)
        except FileExistsError as e:
            sentinel = (target / _SENTINEL).is_file()
            marker = (target / _IN_PROGRESS_MARKER).is_file()
            try:
                is_empty = not any(target.iterdir())
            except OSError:
                is_empty = False
            if sentinel and not marker:
                raise NCPWorkflowError(
                    f"workflow {name!r} version {version!r} is already "
                    f"installed at {target}; remove it first with "
                    f"`python -m ncp_runtime.workflows remove {name} "
                    f"--version {version}` before reinstalling. "
                    f"Installed (name, version) pairs are immutable; there "
                    f"is no --force replace."
                ) from e
            if marker or is_empty:
                raise NCPWorkflowError(
                    f"an incomplete install exists at {target}; it was "
                    f"created by this installer and not completed (another "
                    f"process may still be installing, or a prior attempt "
                    f"crashed). Run `python -m ncp_runtime.workflows "
                    f"remove {name} --version {version}` to clear it, "
                    f"then retry."
                ) from e
            raise NCPWorkflowError(
                f"unexpected content already exists at {target}; it was "
                f"not created by this installer. Inspect and remove it "
                f"before installing."
            ) from e

        # Target is a fresh empty dir we own. Write the in-progress marker
        # FIRST so another process arriving between our mkdir and our
        # first-file-move sees `is_empty=True` and classifies the slot as
        # "incomplete" rather than "foreign".
        marker_path = target / _IN_PROGRESS_MARKER
        try:
            marker_path.write_text(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "started_at": _dt.datetime.now(_dt.timezone.utc).isoformat(
                            timespec="seconds"
                        ),
                        "name": name,
                        "version": version,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            # Move each staged child into target. `shutil.move` under the
            # same filesystem (both paths are under `<home>`) uses
            # `os.rename` per entry, which is atomic. The staged tree
            # ALREADY contains `.install.json`; moving it into target
            # makes the sentinel present, but the marker is still there
            # so list_installed hides the slot until we remove the marker.
            for item in list(extracted_root.iterdir()):
                shutil.move(str(item), str(target / item.name))
            if not (target / _SENTINEL).is_file():
                raise NCPWorkflowError(f"internal error: sentinel missing after move at {target}")
            # Finalize: dropping the marker makes the install visible.
            marker_path.unlink()
        except BaseException:
            # Partial-cleanup-safe failure path.
            #
            # STRATEGY 1 (preferred): atomically rename `target` into a
            # sibling under `<home>/_staging/` (same filesystem, so the
            # rename is a single atomic metadata op on POSIX and
            # Windows). After the rename, the install slot at `target`
            # NO LONGER EXISTS, so no ghost state is possible regardless
            # of what happens to the renamed carcass. The carcass is
            # then rmtree'd on a best-effort basis — any failure there
            # is cosmetic, bounded by the _staging reaper.
            #
            # STRATEGY 2 (fallback when rename fails, e.g. Windows file
            # lock on an open descriptor): DO NOT rmtree the live
            # target. Partial rmtree could remove the in-progress
            # marker while leaving `.install.json` behind — surfacing
            # a ghost-complete install to `_iter_installed`. Instead,
            # the marker written BEFORE the move loop is still inside
            # target; we re-assert it defensively and fail closed.
            # Explicit recovery via `workflows remove` is required to
            # clear the slot.
            import secrets as _secrets

            carcass = _staging_root() / (f".cleanup.{name}.{version}.{_secrets.token_hex(8)}")
            try:
                os.rename(str(target), str(carcass))
            except OSError:
                # Rename-into-quarantine failed. Do NOT rmtree the live
                # target — partial deletion is the attack vector the
                # independent audit flagged. Re-assert the marker if it
                # somehow went missing (it shouldn't; it was written
                # before the move loop), and surface the original
                # exception so the caller knows the slot is wedged.
                try:
                    if not marker_path.is_file():
                        marker_path.write_text(
                            json.dumps(
                                {
                                    "state": "cleanup-rename-failed",
                                    "pid": os.getpid(),
                                    "name": name,
                                    "version": version,
                                }
                            )
                            + "\n",
                            encoding="utf-8",
                        )
                except OSError:
                    # Even the marker rewrite failed. Last-ditch: delete
                    # the sentinel if it exists so no ghost can surface.
                    # If this also fails, the raise below will still
                    # propagate the original error — the user runs
                    # `workflows remove` to finish cleanup.
                    try:
                        (target / _SENTINEL).unlink()
                    except OSError:
                        pass
            else:
                shutil.rmtree(str(carcass), ignore_errors=True)
            raise

    return {
        "name": name,
        "version": version,
        "source": src,
        "sha256": actual_sha,
        "install_root": target,
        "runtime_version": runtime_version,
        "bricks": bricks,
    }


# -- list / show / remove / resolve --------------------------------------


def _iter_installed() -> list[dict[str, Any]]:
    home = workflow_home()
    if not home.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for name_dir in sorted(home.iterdir()):
        if not name_dir.is_dir() or name_dir.name.startswith("_"):
            continue
        name = name_dir.name
        for ver_dir in sorted(name_dir.iterdir()):
            if not ver_dir.is_dir():
                continue
            # Hide:
            #   * anything with the reserved dot-prefix (defensive; our
            #     publish never creates such dirs now that it uses
            #     mkdir-lock, but other tooling or crashes might)
            #   * directories without the `.install.json` sentinel
            #   * directories with an active `.ncp-install.inprogress`
            #     marker — those are either another process's in-flight
            #     install or a crashed one awaiting `remove`.
            if ver_dir.name.startswith("."):
                continue
            if (ver_dir / _IN_PROGRESS_MARKER).is_file():
                continue
            meta_path = ver_dir / _SENTINEL
            if not meta_path.is_file():
                continue
            meta: dict[str, Any]
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if not isinstance(meta, dict):
                    meta = {}
            except json.JSONDecodeError:
                meta = {}
            rows.append(
                {
                    "name": name,
                    "version": ver_dir.name,
                    "install_root": ver_dir,
                    "installed_at": meta.get("installed_at", ""),
                    "source": meta.get("source", ""),
                    "sha256": meta.get("archive_sha256", ""),
                    "runtime_version_at_install": meta.get("runtime_version_at_install", ""),
                    "description": meta.get("description", ""),
                }
            )
    return rows


def list_installed() -> list[dict[str, Any]]:
    """Return every (name, version) currently installed under NCP_WORKFLOW_HOME."""
    return _iter_installed()


def _versions_of(name: str) -> list[dict[str, Any]]:
    _assert_name_safe(name)
    return [row for row in _iter_installed() if row["name"] == name]


def _resolve_single(name: str, version: str | None) -> dict[str, Any]:
    _assert_name_safe(name)
    if version is not None:
        _assert_version_safe(version)
    rows = _versions_of(name)
    if not rows:
        raise NCPWorkflowNotInstalledError(
            f"no installed workflow named {name!r} under {workflow_home()}"
        )
    if version is None:
        if len(rows) > 1:
            raise NCPWorkflowAmbiguousVersionError(
                f"workflow {name!r} has {len(rows)} installed versions "
                f"({[r['version'] for r in rows]}); pass version= explicitly"
            )
        return rows[0]
    for row in rows:
        if row["version"] == version:
            return row
    raise NCPWorkflowNotInstalledError(
        f"workflow {name!r} version {version!r} is not installed; "
        f"available versions: {[r['version'] for r in rows]}"
    )


def show(name: str, *, version: str | None = None) -> dict[str, Any]:
    """Return the workflow.json + install metadata + brick summary."""
    row = _resolve_single(name, version)
    root = row["install_root"]
    wf = _load_workflow_json(root)
    bricks: list[dict[str, Any]] = []
    try:
        graph_obj = _load_yaml(root / wf["graph"])
        refs = _collect_brick_refs(graph_obj, root / wf["graph"])
        for ref in refs:
            bid = ref["brick_id"]
            info = _validate_brick(root / wf["bricks_dir"], bid)
            bricks.append(
                {
                    "brick_id": bid,
                    "version": info["manifest_version"],
                    "digest": info["digest"],
                    "size_bytes": info["size"],
                }
            )
    except NCPSDKError as e:
        bricks = [{"error": str(e)}]
    return {
        "name": row["name"],
        "version": row["version"],
        "install_root": root,
        "workflow_json": wf,
        "install_metadata": {
            "installed_at": row["installed_at"],
            "source": row["source"],
            "sha256": row["sha256"],
            "runtime_version_at_install": row["runtime_version_at_install"],
        },
        "bricks": bricks,
    }


def remove(
    name: str,
    *,
    version: str | None = None,
    all: bool = False,
) -> list[Path]:
    """Delete one or all versions of a workflow.

    `remove` reads the filesystem directly (not via `_iter_installed`) so
    it can also clear incomplete-install zombies — directories that hold
    a `.ncp-install.inprogress` marker from a crashed install. This is
    the explicit recovery path for interrupted installs.

    `name` is validated against `_NAME_RE` BEFORE any path construction,
    and the resolved `<home>/<name>/` directory must be a direct child
    of `<home>`. Prior to this hardening,
    `remove("../anything", all=True)` could delete directories outside
    the workflow home.
    """
    if version is not None:
        _assert_version_safe(version)
    # _name_dir does both _NAME_RE validation and a filesystem-level
    # confinement check (parent must equal workflow_home()). Any name
    # containing a path separator, `..`, or any other out-of-grammar
    # character is rejected before iterdir() or rmtree() touches disk.
    name_dir = _name_dir(name)
    home = workflow_home()
    if not name_dir.is_dir():
        raise NCPWorkflowNotInstalledError(f"no installed workflow named {name!r} under {home}")
    all_versions = [
        p for p in sorted(name_dir.iterdir()) if p.is_dir() and not p.name.startswith(".")
    ]
    if not all_versions:
        raise NCPWorkflowNotInstalledError(f"no installed workflow named {name!r} under {home}")

    if version is not None:
        targets = [p for p in all_versions if p.name == version]
        if not targets:
            raise NCPWorkflowNotInstalledError(
                f"workflow {name!r} version {version!r} is not present; "
                f"available: {[p.name for p in all_versions]}"
            )
    elif not all:
        if len(all_versions) > 1:
            raise NCPWorkflowAmbiguousVersionError(
                f"workflow {name!r} has {len(all_versions)} versions "
                f"({[p.name for p in all_versions]}); pass --version or --all"
            )
        targets = all_versions
    else:
        targets = all_versions

    # Defense-in-depth: each target MUST resolve under name_dir. This
    # catches any future change that might let a crafted version segment
    # escape the per-call _install_root check (version_or_range from an
    # untrusted source, symlinks under <home>, etc.).
    removed: list[Path] = []
    name_dir_resolved = name_dir.resolve(strict=False)
    for root in targets:
        resolved_parent = root.resolve(strict=False).parent
        if resolved_parent != name_dir_resolved:
            raise NCPWorkflowError(f"refusing to remove {root}: resolves outside {name_dir}")
        shutil.rmtree(root)
        removed.append(root)
    try:
        if name_dir.is_dir() and not any(name_dir.iterdir()):
            name_dir.rmdir()
    except OSError:
        pass
    return removed


def resolve(name: str, *, version: str | None = None) -> tuple[Path, Path]:
    """Return (graph_path, brick_dir) for the matching installed workflow."""
    row = _resolve_single(name, version)
    root = row["install_root"]
    wf_path = root / "workflow.json"
    if not wf_path.is_file():
        raise NCPWorkflowError(f"installed workflow root is missing workflow.json: {wf_path}")
    try:
        wf = json.loads(wf_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise NCPWorkflowError(f"installed workflow.json is malformed: {e}") from e
    return (root / wf["graph"], root / wf["bricks_dir"])
