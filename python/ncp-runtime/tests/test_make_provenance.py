# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Unit tests for scripts/make_provenance.py.

Covers both modes (platform / fallback), input validation, atomic
write semantics, SHA-256 + size derivation from the bundled binary,
and the §11 schema shape exactly.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

# scripts/ is a sibling of src/; import by file path.
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import make_provenance  # noqa: E402

_VALID_SHA = "a" * 40
_VALID_SHA_2 = "b" * 40


def _run(argv: list[str]) -> int:
    return make_provenance.main(argv)


def _write_binary(path: Path, body: bytes) -> tuple[str, int]:
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest(), len(body)


# --- platform mode: happy path ------------------------------------------


def test_platform_mode_writes_schema_exactly(tmp_path: Path) -> None:
    bin_path = tmp_path / "ncp"
    bin_sha, bin_size = _write_binary(bin_path, b"\x7fELFfake")
    out = tmp_path / "provenance.json"

    rc = _run(
        [
            "platform",
            "--output",
            str(out),
            "--sdk-version",
            "0.1.0",
            "--runtime-version",
            "0.4.0",
            "--rust-toolchain",
            "1.94.0",
            "--runtime-source-commit",
            _VALID_SHA,
            "--build-target-triple",
            "x86_64-unknown-linux-gnu",
            "--wheel-platform-tag",
            "manylinux_2_28_x86_64",
            "--binary-path",
            str(bin_path),
            "--linux-dt-needed",
            "libc.so.6",
            "--linux-dt-needed",
            "libgcc_s.so.1",
        ]
    )
    assert rc == 0

    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["sdk_version"] == "0.1.0"
    assert doc["runtime_source_commit"] == _VALID_SHA
    assert doc["runtime_version"] == "0.4.0"
    assert doc["rust_toolchain"] == "1.94.0"
    assert doc["build_target_triple"] == "x86_64-unknown-linux-gnu"
    assert doc["wheel_platform_tag"] == "manylinux_2_28_x86_64"
    assert doc["binary_filename"] == "ncp"
    assert doc["binary_sha256"] == f"sha256:{bin_sha}"
    assert doc["binary_size_bytes"] == bin_size
    assert doc["linux_dt_needed_baseline"] == ["libc.so.6", "libgcc_s.so.1"]
    assert doc["build_timestamp_rfc3339"].endswith("Z")
    assert len(doc["build_timestamp_rfc3339"]) == 20


def test_platform_mode_without_dt_needed_omits_key(tmp_path: Path) -> None:
    """macOS/Windows platforms don't populate the Linux DT_NEEDED baseline."""
    bin_path = tmp_path / "ncp.exe"
    _write_binary(bin_path, b"MZ")
    out = tmp_path / "provenance.json"

    rc = _run(
        [
            "platform",
            "--output",
            str(out),
            "--sdk-version",
            "0.1.0",
            "--runtime-version",
            "0.4.0",
            "--rust-toolchain",
            "1.94.0",
            "--runtime-source-commit",
            _VALID_SHA,
            "--build-target-triple",
            "x86_64-pc-windows-msvc",
            "--wheel-platform-tag",
            "win_amd64",
            "--binary-path",
            str(bin_path),
        ]
    )
    assert rc == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert "linux_dt_needed_baseline" not in doc
    assert doc["binary_filename"] == "ncp.exe"


# --- fallback mode: happy path ------------------------------------------


def test_fallback_mode_shape(tmp_path: Path) -> None:
    out = tmp_path / "provenance.json"
    rc = _run(
        [
            "fallback",
            "--output",
            str(out),
            "--sdk-version",
            "0.1.0",
            "--runtime-version",
            "0.4.0",
            "--rust-toolchain",
            "1.94.0",
            "--note",
            "Universal Python-only fallback wheel; install runtime separately.",
        ]
    )
    assert rc == 0

    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["runtime_source_commit"] is None
    assert doc["build_target_triple"] is None
    assert doc["binary_filename"] is None
    assert doc["binary_sha256"] is None
    assert doc["binary_size_bytes"] is None
    assert doc["wheel_platform_tag"] == "any"
    assert doc["note"].startswith("Universal Python-only")
    assert "linux_dt_needed_baseline" not in doc


# --- validation ---------------------------------------------------------


@pytest.mark.parametrize(
    "sha",
    [
        "",
        "A" * 40,
        "a" * 39,
        "a" * 41,
        "ghijklmn" * 5,
        "a" * 40 + " ",
    ],
)
def test_platform_rejects_bad_commit_sha(tmp_path: Path, sha: str) -> None:
    bin_path = tmp_path / "ncp"
    _write_binary(bin_path, b"x")
    rc = _run(
        [
            "platform",
            "--output",
            str(tmp_path / "p.json"),
            "--sdk-version",
            "0.1.0",
            "--runtime-version",
            "0.4.0",
            "--rust-toolchain",
            "1.94.0",
            "--runtime-source-commit",
            sha,
            "--build-target-triple",
            "x86_64-unknown-linux-gnu",
            "--wheel-platform-tag",
            "manylinux_2_28_x86_64",
            "--binary-path",
            str(bin_path),
        ]
    )
    assert rc == 1
    assert not (tmp_path / "p.json").exists(), "no partial write on invalid input"


@pytest.mark.parametrize("ver", ["0.1", "v0.1.0", "0.1.0.0", "not-a-version"])
def test_rejects_malformed_sdk_version(tmp_path: Path, ver: str) -> None:
    rc = _run(
        [
            "fallback",
            "--output",
            str(tmp_path / "p.json"),
            "--sdk-version",
            ver,
            "--runtime-version",
            "0.4.0",
            "--rust-toolchain",
            "1.94.0",
            "--note",
            "x",
        ]
    )
    assert rc == 1
    assert not (tmp_path / "p.json").exists()


def test_platform_rejects_missing_binary(tmp_path: Path) -> None:
    rc = _run(
        [
            "platform",
            "--output",
            str(tmp_path / "p.json"),
            "--sdk-version",
            "0.1.0",
            "--runtime-version",
            "0.4.0",
            "--rust-toolchain",
            "1.94.0",
            "--runtime-source-commit",
            _VALID_SHA,
            "--build-target-triple",
            "x86_64-unknown-linux-gnu",
            "--wheel-platform-tag",
            "manylinux_2_28_x86_64",
            "--binary-path",
            str(tmp_path / "does-not-exist"),
        ]
    )
    assert rc == 1
    assert not (tmp_path / "p.json").exists()


def test_fallback_rejects_empty_note(tmp_path: Path) -> None:
    rc = _run(
        [
            "fallback",
            "--output",
            str(tmp_path / "p.json"),
            "--sdk-version",
            "0.1.0",
            "--runtime-version",
            "0.4.0",
            "--rust-toolchain",
            "1.94.0",
            "--note",
            "   ",
        ]
    )
    assert rc == 1


# --- overwrite semantics -----------------------------------------------


def test_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    out = tmp_path / "p.json"
    out.write_text("original", encoding="utf-8")
    rc = _run(
        [
            "fallback",
            "--output",
            str(out),
            "--sdk-version",
            "0.1.0",
            "--runtime-version",
            "0.4.0",
            "--rust-toolchain",
            "1.94.0",
            "--note",
            "note",
        ]
    )
    assert rc == 1
    assert out.read_text(encoding="utf-8") == "original", "existing file untouched on refusal"


def test_force_replaces_existing(tmp_path: Path) -> None:
    out = tmp_path / "p.json"
    out.write_text("original", encoding="utf-8")
    rc = _run(
        [
            "fallback",
            "--output",
            str(out),
            "--sdk-version",
            "0.1.0",
            "--runtime-version",
            "0.4.0",
            "--rust-toolchain",
            "1.94.0",
            "--note",
            "replacement",
            "--force",
        ]
    )
    assert rc == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["note"] == "replacement"


# --- output format details ---------------------------------------------


def test_output_is_lf_only(tmp_path: Path) -> None:
    out = tmp_path / "p.json"
    rc = _run(
        [
            "fallback",
            "--output",
            str(out),
            "--sdk-version",
            "0.1.0",
            "--runtime-version",
            "0.4.0",
            "--rust-toolchain",
            "1.94.0",
            "--note",
            "x",
        ]
    )
    assert rc == 0
    raw = out.read_bytes()
    assert b"\r\n" not in raw
    assert b"\r" not in raw
    assert raw.endswith(b"\n")


def test_output_keys_are_sorted(tmp_path: Path) -> None:
    out = tmp_path / "p.json"
    _run(
        [
            "fallback",
            "--output",
            str(out),
            "--sdk-version",
            "0.1.0",
            "--runtime-version",
            "0.4.0",
            "--rust-toolchain",
            "1.94.0",
            "--note",
            "x",
        ]
    )
    text = out.read_text(encoding="utf-8")
    keys_in_file = [k for k in text.splitlines() if k.lstrip().startswith('"')]
    assert keys_in_file == sorted(keys_in_file), "keys must be alphabetically ordered"


def test_no_temp_files_leftover_on_failure(tmp_path: Path) -> None:
    out = tmp_path / "p.json"
    rc = _run(
        [
            "platform",
            "--output",
            str(out),
            "--sdk-version",
            "0.1.0",
            "--runtime-version",
            "0.4.0",
            "--rust-toolchain",
            "1.94.0",
            "--runtime-source-commit",
            "not-a-sha",
            "--build-target-triple",
            "x86_64-unknown-linux-gnu",
            "--wheel-platform-tag",
            "manylinux_2_28_x86_64",
            "--binary-path",
            str(tmp_path / "whatever"),
        ]
    )
    assert rc == 1
    assert not list(tmp_path.glob(".provenance-*.tmp"))
