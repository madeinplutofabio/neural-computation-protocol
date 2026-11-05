# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Delivery 2 tests: workflow installer, resolver, list/show/remove.

Safety tests (sha-mismatch, unsafe archive, unsupported scheme, malformed
--sha256, duplicate / case / reserved entries, oversize) do NOT require
NCP_RUNTIME; they must fail BEFORE the runtime-compat probe. Only tests
that genuinely exercise runtime compatibility or workflow execution gate on
NCP_RUNTIME being set.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import tarfile
from pathlib import Path

import pytest

pytest.importorskip("yaml", reason="pyyaml required for workflow installer tests")

from ncp_runtime.types import (  # noqa: E402
    NCPWorkflowAmbiguousVersionError,
    NCPWorkflowDigestError,
    NCPWorkflowError,
    NCPWorkflowNotInstalledError,
    NCPWorkflowRuntimeIncompatibleError,
)
from ncp_runtime.workflows import (  # noqa: E402
    install,
    list_installed,
    package,
    remove,
    resolve,
    show,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
LEAD_QUAL_GRAPH = REPO_ROOT / "examples" / "graphs" / "lead-qualification" / "graph.yaml"
BRICKS_DIR = REPO_ROOT / "examples" / "bricks"
NCP_BIN_ENV = os.environ.get("NCP_RUNTIME")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "wfhome"
    monkeypatch.setenv("NCP_WORKFLOW_HOME", str(h))
    return h


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    d = tmp_path / "dist"
    d.mkdir()
    return d


def _pack_lead_qual(dist: Path, *, version: str = "0.1.0") -> dict:
    out = dist / f"lead-qualification-{version}.tar.gz"
    return package(
        graph=LEAD_QUAL_GRAPH,
        brick_dir=BRICKS_DIR,
        name="org.ncp-examples.lead-qualification",
        version=version,
        runtime_compatibility=">=0.4.0,<0.5.0",
        description="lead qualification v" + version,
        out=out,
    )


def _require_runtime() -> None:
    if not NCP_BIN_ENV:
        pytest.skip("NCP_RUNTIME not set; test requires a real runtime binary")


def _make_tar_with(members: list[tuple[str, bytes, dict]], out: Path) -> str:
    """Build a minimal tar.gz; each member is (name, data, extras)."""
    with tarfile.open(out, "w:gz") as tf:
        for name, data, extras in members:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.type = extras.get("type", tarfile.REGTYPE)
            info.linkname = extras.get("linkname", "")
            tf.addfile(info, io.BytesIO(data))
    return hashlib.sha256(out.read_bytes()).hexdigest()


# -- Happy path: pack -> install -> list -> show -> resolve -> remove ----


def test_pack_install_list_show_resolve_remove_round_trip(home: Path, dist: Path) -> None:
    _require_runtime()
    pack = _pack_lead_qual(dist)
    r = install(pack["path"], sha256=pack["sha256"], binary=NCP_BIN_ENV)
    assert r["install_root"] == home / "org.ncp-examples.lead-qualification" / "0.1.0"
    assert r["install_root"].is_dir()
    assert (r["install_root"] / "workflow.json").is_file()
    assert (r["install_root"] / "graph.yaml").is_file()
    assert (r["install_root"] / "bricks" / "icp-qualifier" / "icp-qualifier.wasm").is_file()
    assert (r["install_root"] / ".install.json").is_file()

    rows = list_installed()
    assert len(rows) == 1
    assert rows[0]["name"] == "org.ncp-examples.lead-qualification"
    assert rows[0]["version"] == "0.1.0"
    assert rows[0]["sha256"] == pack["sha256"]

    info = show("org.ncp-examples.lead-qualification")
    assert info["version"] == "0.1.0"
    assert len(info["bricks"]) == 2

    graph, brick_dir = resolve("org.ncp-examples.lead-qualification")
    assert graph == r["install_root"] / "graph.yaml"
    assert brick_dir == r["install_root"] / "bricks"

    removed = remove("org.ncp-examples.lead-qualification")
    assert r["install_root"] in removed
    assert list_installed() == []


# -- External SHA mismatch (no runtime needed) ---------------------------


def test_install_rejects_external_sha_mismatch(home: Path, dist: Path) -> None:
    pack = _pack_lead_qual(dist)
    bad = "0" * 64
    with pytest.raises(NCPWorkflowDigestError, match="archive sha256 mismatch"):
        install(pack["path"], sha256=bad, binary=NCP_BIN_ENV)
    assert list_installed() == []


def test_install_rejects_malformed_sha_arg(home: Path, dist: Path) -> None:
    pack = _pack_lead_qual(dist)
    with pytest.raises(NCPWorkflowError, match="invalid --sha256"):
        install(pack["path"], sha256="not-hex", binary=NCP_BIN_ENV)


def test_install_accepts_sha_with_prefix(home: Path, dist: Path) -> None:
    _require_runtime()
    pack = _pack_lead_qual(dist)
    r = install(pack["path"], sha256="sha256:" + pack["sha256"], binary=NCP_BIN_ENV)
    assert r["sha256"] == pack["sha256"]


# -- Side-by-side versions + immutability -------------------------------


def test_install_side_by_side_versions(home: Path, dist: Path) -> None:
    _require_runtime()
    p1 = _pack_lead_qual(dist, version="0.1.0")
    p2 = _pack_lead_qual(dist, version="0.2.0")
    install(p1["path"], sha256=p1["sha256"], binary=NCP_BIN_ENV)
    install(p2["path"], sha256=p2["sha256"], binary=NCP_BIN_ENV)
    rows = sorted(list_installed(), key=lambda r: r["version"])
    assert [r["version"] for r in rows] == ["0.1.0", "0.2.0"]


def test_install_ambiguous_resolve(home: Path, dist: Path) -> None:
    _require_runtime()
    p1 = _pack_lead_qual(dist, version="0.1.0")
    install(p1["path"], sha256=p1["sha256"], binary=NCP_BIN_ENV)
    p2 = _pack_lead_qual(dist, version="0.2.0")
    install(p2["path"], sha256=p2["sha256"], binary=NCP_BIN_ENV)
    with pytest.raises(NCPWorkflowAmbiguousVersionError, match="installed versions"):
        resolve("org.ncp-examples.lead-qualification")


def test_install_resolve_specific_version(home: Path, dist: Path) -> None:
    _require_runtime()
    p1 = _pack_lead_qual(dist, version="0.1.0")
    p2 = _pack_lead_qual(dist, version="0.2.0")
    install(p1["path"], sha256=p1["sha256"], binary=NCP_BIN_ENV)
    install(p2["path"], sha256=p2["sha256"], binary=NCP_BIN_ENV)
    g, _bd = resolve("org.ncp-examples.lead-qualification", version="0.1.0")
    assert "0.1.0" in str(g)


def test_install_rejects_duplicate_name_version(home: Path, dist: Path) -> None:
    _require_runtime()
    pack = _pack_lead_qual(dist)
    install(pack["path"], sha256=pack["sha256"], binary=NCP_BIN_ENV)
    with pytest.raises(NCPWorkflowError, match="already installed"):
        install(pack["path"], sha256=pack["sha256"], binary=NCP_BIN_ENV)


# -- Remove -------------------------------------------------------------


def test_remove_specific_and_all(home: Path, dist: Path) -> None:
    _require_runtime()
    p1 = _pack_lead_qual(dist, version="0.1.0")
    p2 = _pack_lead_qual(dist, version="0.2.0")
    install(p1["path"], sha256=p1["sha256"], binary=NCP_BIN_ENV)
    install(p2["path"], sha256=p2["sha256"], binary=NCP_BIN_ENV)
    remove("org.ncp-examples.lead-qualification", version="0.1.0")
    rows = list_installed()
    assert [r["version"] for r in rows] == ["0.2.0"]
    remove("org.ncp-examples.lead-qualification", all=True)
    assert list_installed() == []


def test_remove_not_installed(home: Path) -> None:
    with pytest.raises(NCPWorkflowNotInstalledError):
        remove("org.not.present")


# -- Unsupported source scheme (no runtime needed) ----------------------


@pytest.mark.parametrize("scheme", ["ssh", "git+https", "http", "ftp", "git"])
def test_install_rejects_bad_scheme(home: Path, scheme: str) -> None:
    with pytest.raises(NCPWorkflowError, match="unsupported source scheme"):
        install(
            f"{scheme}://example/does-not-matter.tar.gz",
            sha256="0" * 64,
            binary=NCP_BIN_ENV,
        )


# -- Windows path handling (no runtime needed) ---------------------------


def test_install_native_windows_path_not_mistaken_for_url(
    home: Path, dist: Path, tmp_path: Path
) -> None:
    """On Windows, `C:\\foo` must be treated as a native path, not scheme `c`."""
    # Fabricate a tiny tar so the test runs on both OSes.
    out = tmp_path / "tiny.tar.gz"
    sha = _make_tar_with([("tiny-0.1.0/workflow.json", b"{}", {})], out)
    # The native path case is the default for plain `out`; this also exercises
    # the "no URL scheme" branch of _fetch_to.
    with pytest.raises(NCPWorkflowError) as exc_info:
        install(str(out), sha256=sha, binary=NCP_BIN_ENV)
    # Must fail AFTER fetch (i.e. have reached safe-extract or later),
    # confirming fetch did not reject it as a bad scheme.
    assert "unsupported source scheme" not in str(exc_info.value)


# -- Safe extraction (no runtime needed) --------------------------------


def test_install_rejects_path_traversal_entry(home: Path, dist: Path) -> None:
    out = dist / "traversal.tar.gz"
    sha = _make_tar_with(
        [
            ("evil-0.1.0/workflow.json", b"{}", {}),
            ("evil-0.1.0/../escape.txt", b"nope", {}),
        ],
        out,
    )
    with pytest.raises(NCPWorkflowError, match="parent-traversal"):
        install(out, sha256=sha, binary=NCP_BIN_ENV)


def test_install_rejects_absolute_entry(home: Path, dist: Path) -> None:
    out = dist / "absolute.tar.gz"
    abs_name = "/etc/passwd"
    sha = _make_tar_with(
        [
            ("evil-0.1.0/workflow.json", b"{}", {}),
            (abs_name, b"nope", {}),
        ],
        out,
    )
    with pytest.raises(
        NCPWorkflowError,
        match="absolute path not allowed|exactly one top-level",
    ):
        install(out, sha256=sha, binary=NCP_BIN_ENV)


def test_install_rejects_symlink_entry(home: Path, dist: Path) -> None:
    out = dist / "symlink.tar.gz"
    sha = _make_tar_with(
        [
            ("evil-0.1.0/workflow.json", b"{}", {}),
            (
                "evil-0.1.0/link",
                b"",
                {"type": tarfile.SYMTYPE, "linkname": "../outside"},
            ),
        ],
        out,
    )
    with pytest.raises(NCPWorkflowError, match="non-regular entry"):
        install(out, sha256=sha, binary=NCP_BIN_ENV)


def test_install_rejects_hardlink_entry(home: Path, dist: Path) -> None:
    out = dist / "hardlink.tar.gz"
    sha = _make_tar_with(
        [
            ("evil-0.1.0/workflow.json", b"{}", {}),
            (
                "evil-0.1.0/hard",
                b"",
                {"type": tarfile.LNKTYPE, "linkname": "evil-0.1.0/workflow.json"},
            ),
        ],
        out,
    )
    with pytest.raises(NCPWorkflowError, match="non-regular entry"):
        install(out, sha256=sha, binary=NCP_BIN_ENV)


def test_install_rejects_duplicate_entry(home: Path, dist: Path) -> None:
    out = dist / "dup.tar.gz"
    sha = _make_tar_with(
        [
            ("dup-0.1.0/workflow.json", b"{}", {}),
            ("dup-0.1.0/workflow.json", b"{}", {}),
        ],
        out,
    )
    with pytest.raises(NCPWorkflowError, match="duplicate archive entry"):
        install(out, sha256=sha, binary=NCP_BIN_ENV)


def test_install_rejects_case_collision(home: Path, dist: Path) -> None:
    out = dist / "case.tar.gz"
    sha = _make_tar_with(
        [
            ("case-0.1.0/workflow.json", b"{}", {}),
            ("case-0.1.0/README.md", b"a", {}),
            ("case-0.1.0/readme.md", b"b", {}),
        ],
        out,
    )
    with pytest.raises(NCPWorkflowError, match="case-insensitive collision"):
        install(out, sha256=sha, binary=NCP_BIN_ENV)


def test_install_rejects_windows_reserved_segment(home: Path, dist: Path) -> None:
    out = dist / "reserved.tar.gz"
    sha = _make_tar_with(
        [
            ("reserved-0.1.0/workflow.json", b"{}", {}),
            ("reserved-0.1.0/aux.txt", b"nope", {}),
        ],
        out,
    )
    with pytest.raises(NCPWorkflowError, match="Windows-reserved segment"):
        install(out, sha256=sha, binary=NCP_BIN_ENV)


def test_install_rejects_windows_invalid_chars(home: Path, dist: Path) -> None:
    """Archives must not carry filenames with Windows-invalid characters
    (`<>:"|?*`). `:` is particularly hazardous (NTFS alternate data streams)."""
    for bad_segment in ["foo:bar.txt", 'baz"qux', "pipe|name"]:
        out = dist / f"bad-{hash(bad_segment) & 0xFFFF:x}.tar.gz"
        sha = _make_tar_with(
            [
                ("w-0.1.0/workflow.json", b"{}", {}),
                (f"w-0.1.0/{bad_segment}", b"nope", {}),
            ],
            out,
        )
        with pytest.raises(NCPWorkflowError, match="Windows-invalid character"):
            install(out, sha256=sha, binary=NCP_BIN_ENV)


def test_install_rejects_multi_dot_reserved_segment(home: Path, dist: Path) -> None:
    """NUL.<anything> resolves to NUL on Windows; the reserved-name check
    must split on the FIRST dot, not the last."""
    out = dist / "nul-multidot.tar.gz"
    sha = _make_tar_with(
        [
            ("multidot-0.1.0/workflow.json", b"{}", {}),
            ("multidot-0.1.0/nul.config.json", b"nope", {}),
        ],
        out,
    )
    with pytest.raises(NCPWorkflowError, match="Windows-reserved segment"):
        install(out, sha256=sha, binary=NCP_BIN_ENV)


def test_install_rejects_archive_with_installer_sentinel(home: Path, dist: Path) -> None:
    """Archives must not carry installer-owned sentinel filenames."""
    out = dist / "with-sentinel.tar.gz"
    sha = _make_tar_with(
        [
            ("sent-0.1.0/workflow.json", b"{}", {}),
            ("sent-0.1.0/.install.json", b"hijack", {}),
        ],
        out,
    )
    with pytest.raises(NCPWorkflowError, match="installer-reserved filename"):
        install(out, sha256=sha, binary=NCP_BIN_ENV)


def test_install_rejects_archive_with_inprogress_marker(home: Path, dist: Path) -> None:
    out = dist / "with-marker.tar.gz"
    sha = _make_tar_with(
        [
            ("sent-0.1.0/workflow.json", b"{}", {}),
            ("sent-0.1.0/.ncp-install.inprogress", b"hijack", {}),
        ],
        out,
    )
    with pytest.raises(NCPWorkflowError, match="installer-reserved filename"):
        install(out, sha256=sha, binary=NCP_BIN_ENV)


def test_install_rejects_too_many_entries(
    home: Path, dist: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Entry-count cap: 2 entries, cap 1 -> reject before extraction."""
    monkeypatch.setenv("NCP_WORKFLOW_MAX_ENTRIES", "1")
    out = dist / "too-many.tar.gz"
    sha = _make_tar_with(
        [
            ("many-0.1.0/workflow.json", b"{}", {}),
            ("many-0.1.0/extra.txt", b"x", {}),
        ],
        out,
    )
    with pytest.raises(NCPWorkflowError, match="entries which exceeds"):
        install(out, sha256=sha, binary=NCP_BIN_ENV)


def test_install_rejects_oversize_local_source_before_copy(
    home: Path, dist: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """max_size must cap local/file:// sources at FETCH time, not just at
    extraction time. With a 2 KiB archive and a 1 KiB cap, fetch refuses
    before any copy or extraction work happens."""
    _require_runtime()
    pack = _pack_lead_qual(dist)  # ~28 KiB
    monkeypatch.setenv("NCP_WORKFLOW_MAX_SIZE", "1024")
    with pytest.raises(NCPWorkflowError, match="exceeds .* bytes.*refusing to fetch"):
        install(pack["path"], sha256=pack["sha256"], binary=NCP_BIN_ENV)


def test_install_rejects_file_url_with_non_local_authority(home: Path, dist: Path) -> None:
    """file:// URLs with a non-empty, non-localhost authority (netloc) must
    be rejected — urllib silently discards it, which on Windows would turn
    `file://server/share/x` into the wrong local path."""
    with pytest.raises(NCPWorkflowError, match="non-local authority"):
        install(
            "file://fileserver/dist/x.tar.gz",
            sha256="0" * 64,
            binary=NCP_BIN_ENV,
        )


# -- Path-traversal regression (independent-audit P0) --------------------


def test_remove_rejects_path_traversal_name(home: Path, tmp_path: Path) -> None:
    """P0 regression: remove("../unrelated", all=True) must NOT delete
    directories outside NCP_WORKFLOW_HOME. Independent audit of the
    original patch demonstrated this bug could destroy an unrelated
    directory tree. The name is now validated against _NAME_RE before
    any filesystem path is constructed."""
    # Stage a sibling directory outside the workflow home.
    outside = tmp_path / "outside-the-home"
    (outside / "precious").mkdir(parents=True)
    (outside / "precious" / "secret.txt").write_text("keep me", encoding="utf-8")
    assert (outside / "precious" / "secret.txt").is_file()

    with pytest.raises(NCPWorkflowError, match="invalid workflow name"):
        remove("../outside-the-home", all=True)
    # Nothing outside the home was touched.
    assert (outside / "precious" / "secret.txt").read_text(encoding="utf-8") == "keep me"


@pytest.mark.parametrize(
    "bad_name",
    [
        "../outside",
        "foo/../bar",
        "..",
        "foo bar",
        "",
        "C:/Windows",
        "/etc",
        "foo/bar",
        "foo\\bar",
        "foo:bar",
    ],
)
def test_remove_rejects_malformed_names(home: Path, bad_name: str) -> None:
    """Every malformed or traversal-suggesting name is rejected at the
    entry point of `remove`, before any iterdir() or rmtree()."""
    with pytest.raises(NCPWorkflowError):
        remove(bad_name, all=True)


@pytest.mark.parametrize(
    "bad_name",
    ["../outside", "foo/../bar", "..", "foo bar", "", "foo:bar"],
)
def test_resolve_rejects_malformed_names(home: Path, bad_name: str) -> None:
    with pytest.raises(NCPWorkflowError):
        resolve(bad_name)


@pytest.mark.parametrize("bad_name", ["../x", "x/y", "x..y/z"])
def test_show_rejects_malformed_names(home: Path, bad_name: str) -> None:
    with pytest.raises(NCPWorkflowError):
        show(bad_name)


# -- P1: cleanup cannot leave a ghost-complete install --------------------


def test_install_cleanup_no_ghost_when_quarantine_rename_also_fails(
    home: Path, dist: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Independent-audit regression: mid-move failure PLUS quarantine
    rename failure MUST NOT leave a ghost-complete install at target.
    The fallback path cannot rmtree the live target — partial rmtree
    could remove the in-progress marker while leaving `.install.json`.
    """
    _require_runtime()
    pack = _pack_lead_qual(dist)
    import importlib

    install_mod = importlib.import_module("ncp_runtime.workflows.install")
    real_move = install_mod.shutil.move
    real_rename = install_mod.os.rename
    moves = {"n": 0}

    def failing_move(*args: object, **kwargs: object) -> object:
        moves["n"] += 1
        # Fail after at least one file has moved into target (including
        # possibly `.install.json` itself).
        if moves["n"] == 2:
            raise RuntimeError("injected move failure")
        return real_move(*args, **kwargs)

    def failing_rename(src: str, dst: str) -> None:
        # Fail any rename whose destination is under `_staging/.cleanup.*`
        # (the quarantine path used by the install exception handler).
        # All other renames (atomic publish, etc.) go through unchanged.
        dst_str = str(dst)
        if ".cleanup." in dst_str and "_staging" in dst_str:
            raise OSError(13, "injected quarantine rename failure", dst_str)
        return real_rename(src, dst)

    monkeypatch.setattr(install_mod.shutil, "move", failing_move)
    monkeypatch.setattr(install_mod.os, "rename", failing_rename)

    with pytest.raises(RuntimeError, match="injected move failure"):
        install(pack["path"], sha256=pack["sha256"], binary=NCP_BIN_ENV)

    # Core claim: the install slot MUST NOT be consumable as a completed
    # install. If the directory still exists on disk, the marker must be
    # present OR the sentinel must be absent — otherwise _iter_installed
    # would surface a ghost.
    target = home / "org.ncp-examples.lead-qualification" / "0.1.0"
    if target.exists():
        marker = (target / ".ncp-install.inprogress").is_file()
        sentinel = (target / ".install.json").is_file()
        assert marker or not sentinel, (
            f"GHOST INSTALL: sentinel={sentinel} marker={marker} at {target}"
        )
    assert list_installed() == [], (
        f"list_installed must not surface the incomplete slot: {list_installed()!r}"
    )
    with pytest.raises(NCPWorkflowNotInstalledError):
        resolve("org.ncp-examples.lead-qualification", version="0.1.0")
    # Recovery claim: explicit `remove` clears the zombie slot.
    if target.exists():
        remove("org.ncp-examples.lead-qualification", version="0.1.0")
        assert not target.exists()


def test_install_cleanup_cannot_produce_ghost_complete_install(
    home: Path, dist: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1 regression: if shutil.rmtree in the install failure path leaves
    `.install.json` behind, the previous version of the code would
    surface that partial content as an installed workflow. After the
    atomic-rename-out-of-way fix, no ghost can remain in the install slot."""
    _require_runtime()
    pack = _pack_lead_qual(dist)

    # Simulate a cleanup that is only partially effective: inject an
    # exception into the per-file move loop AFTER at least one file
    # (possibly including `.install.json`) has already been moved into
    # the target. The install itself must still fail AND must still not
    # produce a visible install — the atomic-rename-out-of-way cleanup
    # must prevent any ghost-complete slot.
    import importlib

    install_mod = importlib.import_module("ncp_runtime.workflows.install")
    real_move = install_mod.shutil.move
    moves_done = {"n": 0}

    def failing_move(*args: object, **kwargs: object) -> object:
        moves_done["n"] += 1
        # Fail on the SECOND move so the first file is already inside
        # target when the exception fires.
        if moves_done["n"] == 2:
            raise RuntimeError("injected move failure")
        return real_move(*args, **kwargs)

    monkeypatch.setattr(install_mod.shutil, "move", failing_move)
    with pytest.raises(RuntimeError, match="injected move failure"):
        install(pack["path"], sha256=pack["sha256"], binary=NCP_BIN_ENV)

    # The install slot must either be absent OR classified as incomplete.
    # The list must NOT surface it as an installed workflow.
    rows = list_installed()
    assert rows == [], f"ghost install leaked through: {rows!r}"


def test_install_reaper_drops_stale_staging_but_preserves_recent(home: Path, dist: Path) -> None:
    """Stale _staging/<uuid>/ dirs older than the reap threshold are removed
    on the next install; recent siblings (another in-flight install) are
    preserved."""
    _require_runtime()
    import time

    staging = home / "_staging"
    staging.mkdir(parents=True)
    old = staging / "crashed-crypto-dir"
    old.mkdir()
    # Explicit dead-pid ownership evidence — required under the
    # conservative reaper policy.
    (old / ".lock").write_text("-1", encoding="utf-8")
    (old / "junk").write_bytes(b"x" * 1024)
    # Back-date old by 48h
    stale_ts = time.time() - 48 * 3600
    import os as _os

    _os.utime(old, (stale_ts, stale_ts))
    # Fresh sibling simulating another install in flight.
    fresh = staging / "in-flight-dir"
    fresh.mkdir()
    (fresh / ".lock").write_text(str(_os.getpid()), encoding="utf-8")
    (fresh / "wip").write_bytes(b"y" * 1024)

    pack = _pack_lead_qual(dist)
    install(pack["path"], sha256=pack["sha256"], binary=NCP_BIN_ENV)
    assert not old.exists(), "stale staging dir with dead-pid lock should have been reaped"
    assert fresh.exists(), "recent staging dir must be preserved"


def test_install_reaper_preserves_staging_without_lock(home: Path, dist: Path) -> None:
    """Conservative reaper policy: a staging directory without a `.lock`
    file is ambiguous and MUST NOT be deleted, regardless of age. The
    audit flagged that PID alone is not definitive ownership evidence;
    an absent lock is even less so. Manual cleanup is the required path
    for these."""
    _require_runtime()
    import os as _os
    import time

    staging = home / "_staging"
    staging.mkdir(parents=True)
    ambiguous = staging / "no-lock-mystery-dir"
    ambiguous.mkdir()
    (ambiguous / "junk").write_bytes(b"x" * 1024)
    # Very old — would be reaped under an aggressive policy.
    stale_ts = time.time() - 48 * 3600
    _os.utime(ambiguous, (stale_ts, stale_ts))

    pack = _pack_lead_qual(dist)
    install(pack["path"], sha256=pack["sha256"], binary=NCP_BIN_ENV)
    assert ambiguous.exists(), "a staging dir with no .lock file must be left for manual cleanup"


def test_install_reaper_respects_live_pid_lock_even_on_old_dir(home: Path, dist: Path) -> None:
    """Even a staging dir that exceeds the age threshold is spared if its
    .lock file names a live PID. Closes the audit concern that age alone
    could reap a legitimate long-running installation."""
    _require_runtime()
    import os as _os
    import time

    staging = home / "_staging"
    staging.mkdir(parents=True)
    old_but_live = staging / "live-slow-install"
    old_but_live.mkdir()
    (old_but_live / ".lock").write_text(str(_os.getpid()), encoding="utf-8")
    (old_but_live / "wip").write_bytes(b"y" * 1024)
    stale_ts = time.time() - 48 * 3600
    _os.utime(old_but_live, (stale_ts, stale_ts))

    pack = _pack_lead_qual(dist)
    install(pack["path"], sha256=pack["sha256"], binary=NCP_BIN_ENV)
    assert old_but_live.exists(), "live-pid-locked staging must NOT be reaped"


def test_install_rejects_multi_root_archive(home: Path, dist: Path) -> None:
    out = dist / "multiroot.tar.gz"
    sha = _make_tar_with(
        [
            ("one-0.1.0/workflow.json", b"{}", {}),
            ("two-0.1.0/workflow.json", b"{}", {}),
        ],
        out,
    )
    with pytest.raises(NCPWorkflowError, match="exactly one top-level directory"):
        install(out, sha256=sha, binary=NCP_BIN_ENV)


def test_install_rejects_oversize(home: Path, dist: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pack = _pack_lead_qual(dist)
    monkeypatch.setenv("NCP_WORKFLOW_MAX_SIZE", "1024")
    with pytest.raises(NCPWorkflowError, match="exceeds .* bytes"):
        install(pack["path"], sha256=pack["sha256"], binary=NCP_BIN_ENV)


# -- Runtime compatibility (needs runtime) -----------------------------


def test_install_rejects_incompatible_runtime(home: Path, dist: Path) -> None:
    _require_runtime()
    pack = package(
        graph=LEAD_QUAL_GRAPH,
        brick_dir=BRICKS_DIR,
        name="org.ncp-examples.lead-qualification",
        version="9.9.9",
        runtime_compatibility="=99.0.0",
        out=dist / "incompatible.tar.gz",
    )
    with pytest.raises(NCPWorkflowRuntimeIncompatibleError):
        install(pack["path"], sha256=pack["sha256"], binary=NCP_BIN_ENV)


# -- Tampered brick inside otherwise-valid archive (needs runtime) -----


def test_install_rejects_tampered_brick_despite_matching_outer_sha(
    home: Path, dist: Path, tmp_path: Path
) -> None:
    _require_runtime()
    staged = tmp_path / "bricks"
    shutil.copytree(BRICKS_DIR / "icp-qualifier", staged / "icp-qualifier")
    shutil.copytree(BRICKS_DIR / "company-profile-lookup", staged / "company-profile-lookup")

    good_out = dist / "clean-0.1.0.tar.gz"
    package(
        graph=LEAD_QUAL_GRAPH,
        brick_dir=staged,
        name="org.ncp-examples.lead-qualification",
        version="0.1.0",
        runtime_compatibility=">=0.4.0,<0.5.0",
        out=good_out,
    )

    stage = tmp_path / "rewrite"
    stage.mkdir()
    with tarfile.open(good_out, "r:gz") as tf:
        tf.extractall(stage)
    root = stage / "org.ncp-examples.lead-qualification-0.1.0"
    wasm = root / "bricks" / "icp-qualifier" / "icp-qualifier.wasm"
    b = bytearray(wasm.read_bytes())
    b[1000] ^= 0x01
    wasm.write_bytes(bytes(b))

    tampered = dist / "tampered-0.1.0.tar.gz"
    with tarfile.open(tampered, "w:gz") as tf:
        for p in sorted(root.rglob("*")):
            arcname = "org.ncp-examples.lead-qualification-0.1.0/" + p.relative_to(root).as_posix()
            tf.add(p, arcname=arcname, recursive=False)
    tampered_sha = hashlib.sha256(tampered.read_bytes()).hexdigest()

    with pytest.raises(NCPWorkflowDigestError, match="digest mismatch|size mismatch"):
        install(tampered, sha256=tampered_sha, binary=NCP_BIN_ENV)


# -- file:// source (needs runtime) ------------------------------------


def test_install_from_file_url(home: Path, dist: Path) -> None:
    _require_runtime()
    pack = _pack_lead_qual(dist)
    url = pack["path"].as_uri()
    r = install(url, sha256=pack["sha256"], binary=NCP_BIN_ENV)
    assert r["source"] == url


# -- Concurrent-install race (reviewer's pre-ship correctness check) ----


def test_install_refuses_foreign_target_with_content(home: Path, dist: Path) -> None:
    """A foreign (non-empty) target dir without our marker must be refused
    as 'unexpected content', not overwritten."""
    _require_runtime()
    pack = _pack_lead_qual(dist)
    target = home / "org.ncp-examples.lead-qualification" / "0.1.0"
    target.mkdir(parents=True)
    (target / "someone-elses-file.txt").write_text("hi", encoding="utf-8")
    with pytest.raises(NCPWorkflowError, match="unexpected content"):
        install(pack["path"], sha256=pack["sha256"], binary=NCP_BIN_ENV)
    assert (target / "someone-elses-file.txt").is_file()


def test_install_rejects_zombie_target_until_removed(home: Path, dist: Path) -> None:
    """A crashed install leaves a target dir with a `.ncp-install.inprogress`
    marker. The next install must REFUSE with an explicit "incomplete install"
    error (never silently overwrite or auto-clean), must stay refused across
    retries without an explicit `remove`, and must succeed after `remove`."""
    _require_runtime()
    pack = _pack_lead_qual(dist)
    # Simulate the exact state a crashed install would leave behind.
    target = home / "org.ncp-examples.lead-qualification" / "0.1.0"
    target.mkdir(parents=True)
    (target / ".ncp-install.inprogress").write_text(
        '{"pid": 0, "started_at": "..."}', encoding="utf-8"
    )
    (target / "workflow.json").write_text('{"schema_version":"ncp-workflow-0.1"}', encoding="utf-8")
    # list_installed hides this (marker present).
    assert list_installed() == []

    # First retry: refused with "incomplete install".
    with pytest.raises(NCPWorkflowError, match="incomplete install"):
        install(pack["path"], sha256=pack["sha256"], binary=NCP_BIN_ENV)
    # The zombie is NOT touched.
    assert target.exists()
    assert (target / ".ncp-install.inprogress").is_file()

    # Second retry without recovery: still refused (no silent overwrite).
    with pytest.raises(NCPWorkflowError, match="incomplete install"):
        install(pack["path"], sha256=pack["sha256"], binary=NCP_BIN_ENV)

    # Explicit recovery via `remove`.
    remove("org.ncp-examples.lead-qualification", version="0.1.0")
    assert not target.exists()

    # Now install succeeds cleanly.
    r = install(pack["path"], sha256=pack["sha256"], binary=NCP_BIN_ENV)
    assert r["install_root"] == target
    assert (target / ".install.json").is_file()
    assert not (target / ".ncp-install.inprogress").exists()


def test_install_refuses_empty_external_target_even_concurrently(home: Path, dist: Path) -> None:
    """The POSIX empty-directory overwrite vector: an external process or
    operator creates `<home>/<name>/<version>/` as an empty dir between our
    preflight and our publish. mkdir-lock refuses it — the publish operation
    cannot replace an existing installation, even an empty one."""
    _require_runtime()
    pack = _pack_lead_qual(dist)
    target = home / "org.ncp-examples.lead-qualification" / "0.1.0"
    target.mkdir(parents=True)
    # Empty target, no marker, no sentinel.
    assert list(target.iterdir()) == []
    with pytest.raises(NCPWorkflowError, match="incomplete install|unexpected content"):
        install(pack["path"], sha256=pack["sha256"], binary=NCP_BIN_ENV)
    # Still empty — not overwritten.
    assert target.exists()
    assert list(target.iterdir()) == []


def test_concurrent_install_only_one_wins(home: Path, dist: Path) -> None:
    """Two threads attempting to install the same (name, version) simultaneously:
    exactly one succeeds, the other raises 'already installed'. No partial state
    is visible to list_installed()."""
    import threading

    _require_runtime()
    pack = _pack_lead_qual(dist)
    barrier = threading.Barrier(2)
    results: list[tuple[str, Exception | None]] = []
    lock = threading.Lock()

    def worker(tag: str) -> None:
        barrier.wait()
        try:
            install(pack["path"], sha256=pack["sha256"], binary=NCP_BIN_ENV)
            with lock:
                results.append((tag, None))
        except Exception as e:  # noqa: BLE001
            with lock:
                results.append((tag, e))

    threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    successes = [r for r in results if r[1] is None]
    failures = [r for r in results if r[1] is not None]
    assert len(successes) == 1, f"expected exactly one winner, got {results!r}"
    assert len(failures) == 1
    assert isinstance(failures[0][1], NCPWorkflowError)
    # Depending on timing the loser either sees a completed install
    # (sentinel present, no marker) or an in-progress one (marker
    # present / target empty). Both are correct.
    msg = str(failures[0][1])
    assert "already installed" in msg or "incomplete install" in msg, msg
    # And the install surfaced to list_installed is complete (has .install.json).
    rows = list_installed()
    assert len(rows) == 1
    assert (rows[0]["install_root"] / ".install.json").is_file()


def test_install_partial_state_hidden_from_list(home: Path, dist: Path) -> None:
    """A <name>/<version>/ directory is hidden by list_installed when either
    (a) the sentinel .install.json is absent or (b) the .ncp-install.inprogress
    marker is present. Both conditions mean "not a complete install"."""
    # (a) sentinel absent
    no_sentinel = home / "org.phantom.workflow" / "0.0.1"
    no_sentinel.mkdir(parents=True)
    (no_sentinel / "workflow.json").write_text("{}", encoding="utf-8")

    # (b) marker present even WITH sentinel
    with_marker = home / "org.incomplete.workflow" / "0.1.0"
    with_marker.mkdir(parents=True)
    (with_marker / ".install.json").write_text("{}", encoding="utf-8")
    (with_marker / ".ncp-install.inprogress").write_text('{"pid": 0}', encoding="utf-8")

    rows = list_installed()
    assert rows == []
