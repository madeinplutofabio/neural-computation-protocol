# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Unit tests for scripts/retag_wheel.py.

Builds synthetic PEP 427 wheels in memory, retags them, and verifies
WHEEL metadata, RECORD manifest, filename, and per-entry
external_attr (file-mode) preservation. The 0755 bit on the bundled
``ncp`` binary is the key invariant to protect for POSIX platform
wheels.
"""

from __future__ import annotations

import base64
import hashlib
import stat
import sys
import zipfile
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import retag_wheel  # noqa: E402

_NAME = "ncp_runtime"
_VERSION = "0.1.0"
_DIST_INFO = f"{_NAME}-{_VERSION}.dist-info"
_WHEEL_FILE = f"{_DIST_INFO}/WHEEL"
_RECORD_FILE = f"{_DIST_INFO}/RECORD"
_METADATA_FILE = f"{_DIST_INFO}/METADATA"


def _record_entry(path: str, data: bytes) -> str:
    h = hashlib.sha256(data).digest()
    b64 = base64.urlsafe_b64encode(h).decode("ascii").rstrip("=")
    return f"{path},sha256={b64},{len(data)}"


def _build_source_wheel(
    tmp_path: Path,
    *,
    include_ncp: bool = True,
    ncp_mode: int = 0o755,
) -> Path:
    """Build a synthetic py3-none-any wheel with a bundled binary and
    executable file-mode bits, mimicking what hatchling emits on POSIX.
    """
    wheel_text = (
        "Wheel-Version: 1.0\nGenerator: synthetic-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    )
    metadata_text = f"Metadata-Version: 2.4\nName: {_NAME}\nVersion: {_VERSION}\n"
    init_bytes = b"__version__ = '0.1.0'\n"
    bundled_bytes = b"\x7fELFfakebinary"
    bundled_prov = b'{"sdk_version":"0.1.0"}\n'

    files: list[tuple[str, bytes, int]] = [
        (f"{_NAME}/__init__.py", init_bytes, 0o644),
        (_METADATA_FILE, metadata_text.encode("utf-8"), 0o644),
        (_WHEEL_FILE, wheel_text.encode("utf-8"), 0o644),
    ]
    if include_ncp:
        files.append((f"{_NAME}/_bundled/ncp", bundled_bytes, ncp_mode))
    files.append((f"{_NAME}/_bundled/provenance.json", bundled_prov, 0o644))

    record_lines = [_record_entry(p, d) for p, d, _ in files]
    record_lines.append(f"{_RECORD_FILE},,")
    record_bytes = ("\n".join(record_lines) + "\n").encode("utf-8")
    files.append((_RECORD_FILE, record_bytes, 0o644))

    out = tmp_path / f"{_NAME}-{_VERSION}-py3-none-any.whl"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for path, data, mode in files:
            info = zipfile.ZipInfo(filename=path)
            info.external_attr = (mode & 0o7777) << 16
            info.create_system = 3  # Unix
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return out


def _wheel_entry_mode(wheel_path: Path, member: str) -> int:
    with zipfile.ZipFile(wheel_path) as z:
        info = z.getinfo(member)
        return (info.external_attr >> 16) & 0o7777


def _wheel_tag(wheel_path: Path) -> str:
    with zipfile.ZipFile(wheel_path) as z:
        text = z.read(_WHEEL_FILE).decode("utf-8")
    for line in text.splitlines():
        if line.startswith("Tag:"):
            return line.split("Tag:", 1)[1].strip()
    raise AssertionError("no Tag: line in WHEEL")


# --- happy path --------------------------------------------------------


def test_retag_linux_filename_and_tag(tmp_path: Path) -> None:
    src = _build_source_wheel(tmp_path)
    outdir = tmp_path / "out"
    rc = retag_wheel.main(
        [
            "--wheel",
            str(src),
            "--platform-tag",
            "manylinux_2_28_x86_64",
            "--outdir",
            str(outdir),
        ]
    )
    assert rc == 0
    produced = list(outdir.glob("*.whl"))
    assert len(produced) == 1
    assert produced[0].name == (f"{_NAME}-{_VERSION}-py3-none-manylinux_2_28_x86_64.whl")
    assert _wheel_tag(produced[0]) == "py3-none-manylinux_2_28_x86_64"


@pytest.mark.parametrize(
    "tag,expected_name_suffix",
    [
        ("manylinux_2_28_x86_64", "py3-none-manylinux_2_28_x86_64.whl"),
        ("macosx_11_0_arm64", "py3-none-macosx_11_0_arm64.whl"),
        ("macosx_10_13_x86_64", "py3-none-macosx_10_13_x86_64.whl"),
        ("win_amd64", "py3-none-win_amd64.whl"),
    ],
)
def test_retag_each_platform(tmp_path: Path, tag: str, expected_name_suffix: str) -> None:
    src = _build_source_wheel(tmp_path)
    outdir = tmp_path / tag
    rc = retag_wheel.main(
        [
            "--wheel",
            str(src),
            "--platform-tag",
            tag,
            "--outdir",
            str(outdir),
        ]
    )
    assert rc == 0
    produced = list(outdir.glob("*.whl"))
    assert produced[0].name.endswith(expected_name_suffix)


# --- preservation guarantees ------------------------------------------


def test_executable_bit_preserved_on_bundled_ncp(tmp_path: Path) -> None:
    """The POSIX 0755 bit on ``_bundled/ncp`` must survive retag,
    otherwise ``pip install`` on Linux/macOS extracts a non-executable
    file and ``NCP().run()`` fails with a permission error.
    """
    src = _build_source_wheel(tmp_path, ncp_mode=0o755)
    outdir = tmp_path / "out"
    rc = retag_wheel.main(
        [
            "--wheel",
            str(src),
            "--platform-tag",
            "manylinux_2_28_x86_64",
            "--outdir",
            str(outdir),
        ]
    )
    assert rc == 0
    produced = next(outdir.glob("*.whl"))
    ncp_mode = _wheel_entry_mode(produced, f"{_NAME}/_bundled/ncp")
    assert ncp_mode == 0o755, f"expected 0755, got {oct(ncp_mode)}"
    assert ncp_mode & stat.S_IXUSR, "owner-execute bit missing"
    assert ncp_mode & stat.S_IXGRP, "group-execute bit missing"
    assert ncp_mode & stat.S_IXOTH, "other-execute bit missing"


def test_non_executable_entries_preserved(tmp_path: Path) -> None:
    """0644 entries stay 0644; the retag doesn't widen permissions."""
    src = _build_source_wheel(tmp_path)
    outdir = tmp_path / "out"
    retag_wheel.main(
        [
            "--wheel",
            str(src),
            "--platform-tag",
            "manylinux_2_28_x86_64",
            "--outdir",
            str(outdir),
        ]
    )
    produced = next(outdir.glob("*.whl"))
    assert _wheel_entry_mode(produced, f"{_NAME}/__init__.py") == 0o644
    assert _wheel_entry_mode(produced, _METADATA_FILE) == 0o644


def test_record_wheel_entry_recomputed(tmp_path: Path) -> None:
    src = _build_source_wheel(tmp_path)
    outdir = tmp_path / "out"
    retag_wheel.main(
        [
            "--wheel",
            str(src),
            "--platform-tag",
            "manylinux_2_28_x86_64",
            "--outdir",
            str(outdir),
        ]
    )
    produced = next(outdir.glob("*.whl"))
    with zipfile.ZipFile(produced) as z:
        new_wheel_bytes = z.read(_WHEEL_FILE)
        new_record = z.read(_RECORD_FILE).decode("utf-8")

    expected_hash = hashlib.sha256(new_wheel_bytes).digest()
    expected_b64 = base64.urlsafe_b64encode(expected_hash).decode("ascii").rstrip("=")
    expected_line = f"{_WHEEL_FILE},sha256={expected_b64},{len(new_wheel_bytes)}"
    assert expected_line in new_record.splitlines()


def test_record_entries_for_other_files_unchanged(tmp_path: Path) -> None:
    """Non-WHEEL RECORD lines must be preserved verbatim."""
    src = _build_source_wheel(tmp_path)
    with zipfile.ZipFile(src) as z:
        src_record = z.read(_RECORD_FILE).decode("utf-8")
    src_entries = {line.rsplit(",", 2)[0]: line for line in src_record.splitlines() if line}

    outdir = tmp_path / "out"
    retag_wheel.main(
        [
            "--wheel",
            str(src),
            "--platform-tag",
            "manylinux_2_28_x86_64",
            "--outdir",
            str(outdir),
        ]
    )
    produced = next(outdir.glob("*.whl"))
    with zipfile.ZipFile(produced) as z:
        new_record = z.read(_RECORD_FILE).decode("utf-8")

    for line in new_record.splitlines():
        if not line:
            continue
        path = line.rsplit(",", 2)[0]
        if path == _WHEEL_FILE:
            continue
        assert src_entries.get(path) == line, f"RECORD entry for {path} changed unexpectedly"


# --- WHEEL metadata details --------------------------------------------


def test_wheel_file_tag_replaced_only_once(tmp_path: Path) -> None:
    """If the source wheel has multiple Tag: lines, retag collapses
    them to a single new Tag: and drops the rest.
    """
    src = _build_source_wheel(tmp_path)
    # Rewrite the source wheel to include two Tag: lines
    with zipfile.ZipFile(src, "r") as z:
        payload = {name: z.read(name) for name in z.namelist()}
    wheel_text = payload[_WHEEL_FILE].decode("utf-8")
    wheel_text += "Tag: py2-none-any\n"
    payload[_WHEEL_FILE] = wheel_text.encode("utf-8")

    src2 = tmp_path / f"{_NAME}-{_VERSION}-py3-none-any.whl"
    src2.unlink()
    with zipfile.ZipFile(src2, "w", zipfile.ZIP_DEFLATED) as z:
        for path, data in payload.items():
            info = zipfile.ZipInfo(filename=path)
            info.external_attr = 0o644 << 16
            info.create_system = 3
            z.writestr(info, data)

    outdir = tmp_path / "out"
    retag_wheel.main(
        [
            "--wheel",
            str(src2),
            "--platform-tag",
            "manylinux_2_28_x86_64",
            "--outdir",
            str(outdir),
        ]
    )
    produced = next(outdir.glob("*.whl"))
    with zipfile.ZipFile(produced) as z:
        text = z.read(_WHEEL_FILE).decode("utf-8")
    tag_lines = [ln for ln in text.splitlines() if ln.startswith("Tag:")]
    assert tag_lines == ["Tag: py3-none-manylinux_2_28_x86_64"]


# --- rejection paths ---------------------------------------------------


def test_rejects_malformed_source_filename(tmp_path: Path) -> None:
    bad = tmp_path / "not-a-wheel.zip"
    bad.write_bytes(b"\x50\x4b\x03\x04")
    rc = retag_wheel.main(
        [
            "--wheel",
            str(bad),
            "--platform-tag",
            "manylinux_2_28_x86_64",
            "--outdir",
            str(tmp_path / "out"),
        ]
    )
    assert rc == 1


def test_rejects_unsafe_platform_tag(tmp_path: Path) -> None:
    src = _build_source_wheel(tmp_path)
    rc = retag_wheel.main(
        [
            "--wheel",
            str(src),
            "--platform-tag",
            "evil tag; rm -rf",
            "--outdir",
            str(tmp_path / "out"),
        ]
    )
    assert rc == 1


def test_refuses_to_overwrite_existing_destination(tmp_path: Path) -> None:
    src = _build_source_wheel(tmp_path)
    outdir = tmp_path / "out"
    retag_wheel.main(
        [
            "--wheel",
            str(src),
            "--platform-tag",
            "manylinux_2_28_x86_64",
            "--outdir",
            str(outdir),
        ]
    )
    # Second run with same target must refuse
    rc = retag_wheel.main(
        [
            "--wheel",
            str(src),
            "--platform-tag",
            "manylinux_2_28_x86_64",
            "--outdir",
            str(outdir),
        ]
    )
    assert rc == 1


def test_rejects_missing_source(tmp_path: Path) -> None:
    rc = retag_wheel.main(
        [
            "--wheel",
            str(tmp_path / "nope.whl"),
            "--platform-tag",
            "manylinux_2_28_x86_64",
            "--outdir",
            str(tmp_path / "out"),
        ]
    )
    assert rc == 1


# --- --replace flag ----------------------------------------------------


def test_replace_deletes_source(tmp_path: Path) -> None:
    src = _build_source_wheel(tmp_path)
    outdir = tmp_path / "out"
    rc = retag_wheel.main(
        [
            "--wheel",
            str(src),
            "--platform-tag",
            "manylinux_2_28_x86_64",
            "--outdir",
            str(outdir),
            "--replace",
        ]
    )
    assert rc == 0
    assert not src.exists(), "--replace should remove the source wheel"
    assert len(list(outdir.glob("*.whl"))) == 1


# --- atomicity ---------------------------------------------------------


def test_no_temp_file_leftover_on_failure(tmp_path: Path) -> None:
    outdir = tmp_path / "out"
    outdir.mkdir()
    rc = retag_wheel.main(
        [
            "--wheel",
            str(tmp_path / "missing.whl"),
            "--platform-tag",
            "manylinux_2_28_x86_64",
            "--outdir",
            str(outdir),
        ]
    )
    assert rc == 1
    assert not list(outdir.glob(".retag-*.whl.tmp"))
