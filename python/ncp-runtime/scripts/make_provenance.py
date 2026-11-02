# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori
"""Generator for ``_bundled/provenance.json`` using the frozen schema in
``docs/PYTHON_SDK.md`` §11.

Produces the exact schema from §11, writing to the output path given on
the command line. Called from the publish-sdk.yml per-platform build
jobs (platform mode) and the fallback-wheel build job (fallback mode).

Platform mode (one platform wheel):
  python scripts/make_provenance.py platform \
    --output src/ncp_runtime/_bundled/provenance.json \
    --sdk-version 0.1.0 \
    --runtime-version 0.3.6 \
    --rust-toolchain 1.94.0 \
    --runtime-source-commit <40-hex-sha-from-git-rev-parse-HEAD> \
    --build-target-triple x86_64-unknown-linux-gnu \
    --wheel-platform-tag manylinux_2_28_x86_64 \
    --binary-path path/to/ncp \
    [--linux-dt-needed libc.so.6 --linux-dt-needed libgcc_s.so.1 ...]

Fallback mode (py3-none-any wheel, no bundled binary):
  python scripts/make_provenance.py fallback \
    --output src/ncp_runtime/_bundled/provenance.json \
    --sdk-version 0.1.0 \
    --runtime-version 0.3.6 \
    --rust-toolchain 1.94.0 \
    --note "Universal Python-only fallback wheel; see docs/PYTHON_SDK.md §11."

Behavior:
  - JSON output with sorted keys, indent=2, LF-only newlines.
  - Atomic write (temp + os.replace within the same directory).
  - Refuses to overwrite an existing destination without --force.
  - Validates every field BEFORE touching disk; no partial writes on invalid input.
  - Platform mode: binary_filename, binary_sha256, binary_size_bytes derived
    from the binary at --binary-path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_SEMVER_LIKE = re.compile(r"^\d+\.\d+\.\d+(?:[-+].*)?$")
_TAG_SAFE = re.compile(r"^[A-Za-z0-9_.+-]+$")
_TARGET_TRIPLE = re.compile(r"^[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+){2,3}$")


def _validate_sha40(value: str, field: str) -> None:
    if not _HEX40.fullmatch(value):
        raise ValueError(f"{field} must be 40 lowercase hex chars; got {value!r}")


def _validate_semverlike(value: str, field: str) -> None:
    if not _SEMVER_LIKE.fullmatch(value):
        raise ValueError(f"{field} must look like X.Y.Z; got {value!r}")


def _validate_tagsafe(value: str, field: str) -> None:
    if not _TAG_SAFE.fullmatch(value):
        raise ValueError(f"{field} contains unsafe characters; got {value!r}")


def _validate_target_triple(value: str, field: str) -> None:
    if not _TARGET_TRIPLE.fullmatch(value):
        raise ValueError(f"{field} must look like a Rust target triple; got {value!r}")


def _sha256_and_size(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _rfc3339_now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_atomic(path: Path, doc: dict, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"refusing to overwrite {path}; pass --force to replace")
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=".provenance-",
            suffix=".tmp",
            delete=False,
        ) as tf:
            temp_path = Path(tf.name)
            tf.write(body)
            tf.flush()
            os.fsync(tf.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _build_platform(args: argparse.Namespace) -> dict:
    _validate_semverlike(args.sdk_version, "--sdk-version")
    _validate_semverlike(args.runtime_version, "--runtime-version")
    _validate_semverlike(args.rust_toolchain, "--rust-toolchain")
    _validate_sha40(args.runtime_source_commit, "--runtime-source-commit")
    _validate_target_triple(args.build_target_triple, "--build-target-triple")
    _validate_tagsafe(args.wheel_platform_tag, "--wheel-platform-tag")

    binary_path = Path(args.binary_path).resolve()
    if not binary_path.is_file():
        raise FileNotFoundError(f"binary not found: {binary_path}")
    sha, size = _sha256_and_size(binary_path)

    doc: dict = {
        "sdk_version": args.sdk_version,
        "runtime_source_commit": args.runtime_source_commit,
        "runtime_version": args.runtime_version,
        "rust_toolchain": args.rust_toolchain,
        "build_target_triple": args.build_target_triple,
        "wheel_platform_tag": args.wheel_platform_tag,
        "binary_filename": binary_path.name,
        "binary_sha256": f"sha256:{sha}",
        "binary_size_bytes": size,
        "build_timestamp_rfc3339": _rfc3339_now_utc(),
    }
    if args.linux_dt_needed:
        doc["linux_dt_needed_baseline"] = list(args.linux_dt_needed)
    return doc


def _build_fallback(args: argparse.Namespace) -> dict:
    _validate_semverlike(args.sdk_version, "--sdk-version")
    _validate_semverlike(args.runtime_version, "--runtime-version")
    _validate_semverlike(args.rust_toolchain, "--rust-toolchain")
    if not args.note.strip():
        raise ValueError("--note must be a non-empty string")

    return {
        "sdk_version": args.sdk_version,
        "runtime_source_commit": None,
        "runtime_version": args.runtime_version,
        "rust_toolchain": args.rust_toolchain,
        "build_target_triple": None,
        "wheel_platform_tag": "any",
        "binary_filename": None,
        "binary_sha256": None,
        "binary_size_bytes": None,
        "build_timestamp_rfc3339": _rfc3339_now_utc(),
        "note": args.note,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    pp = sub.add_parser("platform", help="Platform wheel provenance (with bundled binary)")
    pp.add_argument("--output", type=Path, required=True)
    pp.add_argument("--sdk-version", required=True)
    pp.add_argument("--runtime-version", required=True)
    pp.add_argument("--rust-toolchain", required=True)
    pp.add_argument(
        "--runtime-source-commit",
        required=True,
        help="40-char git SHA from `git rev-parse HEAD` inside the checkout "
        "that built the binary. Do NOT use ${{ github.sha }} directly.",
    )
    pp.add_argument(
        "--build-target-triple",
        required=True,
        help="Rust target triple, e.g. x86_64-unknown-linux-gnu",
    )
    pp.add_argument(
        "--wheel-platform-tag", required=True, help="Wheel platform tag, e.g. manylinux_2_28_x86_64"
    )
    pp.add_argument(
        "--binary-path",
        required=True,
        help="Path to the built binary; filename/sha256/size are derived",
    )
    pp.add_argument(
        "--linux-dt-needed",
        action="append",
        default=[],
        help="DT_NEEDED entry (repeat for each). Linux-only; omit on macOS/Windows.",
    )
    pp.add_argument(
        "--force", action="store_true", help="Overwrite the output path if it already exists"
    )

    pf = sub.add_parser("fallback", help="Universal py3-none-any fallback wheel provenance")
    pf.add_argument("--output", type=Path, required=True)
    pf.add_argument("--sdk-version", required=True)
    pf.add_argument("--runtime-version", required=True)
    pf.add_argument("--rust-toolchain", required=True)
    pf.add_argument(
        "--note",
        required=True,
        help="One sentence explaining what this wheel is and how to install a runtime",
    )
    pf.add_argument(
        "--force", action="store_true", help="Overwrite the output path if it already exists"
    )

    args = parser.parse_args(argv)

    try:
        if args.mode == "platform":
            doc = _build_platform(args)
        else:
            doc = _build_fallback(args)
        _write_atomic(args.output, doc, args.force)
    except (OSError, ValueError, FileExistsError, FileNotFoundError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"[OK] wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
