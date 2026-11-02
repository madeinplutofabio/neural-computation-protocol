# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori
"""Retag a py3-none-any wheel to a specific platform tag.

Rewrites WHEEL metadata and the RECORD manifest, renames the output
file, and preserves every entry's external_attr (file-mode bits).
This is the preserving-executable-bit guarantee that the design
doc requires for the POSIX bundled ``ncp`` binary (mode 0755).

Called by publish-sdk.yml's per-platform build jobs after:
  1. The release ncp binary is placed at src/ncp_runtime/_bundled/ncp[.exe]
     (chmod 0755 on POSIX before build; hatchling records the mode).
  2. ``python -m build --wheel`` produces a py3-none-any wheel in dist/.
  3. make_provenance.py has written _bundled/provenance.json.

Usage:
  python scripts/retag_wheel.py \
    --wheel dist/ncp_runtime-0.1.0-py3-none-any.whl \
    --platform-tag manylinux_2_28_x86_64 \
    --outdir dist/

Behavior:
  - The source wheel is left in place by default; pass ``--replace``
    to delete it after the retag succeeds.
  - The retagged wheel is written atomically (temp + os.replace within
    the same directory). The destination filename is refused if it
    already exists.
  - All ZipInfo metadata (filename, date_time, create_system,
    external_attr, compress_type, flag_bits) is preserved per-entry.
  - WHEEL file: Tag: line replaced with python-abi-platform. Extra
    Tag: lines in the source are dropped (one wheel, one tag).
  - RECORD manifest: the WHEEL entry's hash/size are recomputed; every
    other entry is preserved verbatim.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

_WHEEL_FILENAME_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9_.]+)-(?P<ver>[^-]+)-"
    r"(?P<py>[^-]+)-(?P<abi>[^-]+)-(?P<plat>[^.]+)\.whl$"
)
_TAG_SAFE = re.compile(r"^[A-Za-z0-9_.+]+$")


def _record_hash(data: bytes) -> str:
    h = hashlib.sha256(data).digest()
    b64 = base64.urlsafe_b64encode(h).decode("ascii").rstrip("=")
    return f"sha256={b64}"


def _retag_wheel_bytes(
    data: bytes,
    name: str,
    version: str,
    python_tag: str,
    abi_tag: str,
    platform_tag: str,
) -> bytes:
    src_buf = io.BytesIO(data)
    with zipfile.ZipFile(src_buf, "r") as src:
        dist_info = f"{name}-{version}.dist-info"
        wheel_file = f"{dist_info}/WHEEL"
        record_file = f"{dist_info}/RECORD"

        names = set(src.namelist())
        if wheel_file not in names:
            raise ValueError(f"{wheel_file} not found in wheel")
        if record_file not in names:
            raise ValueError(f"{record_file} not found in wheel")

        wheel_text = src.read(wheel_file).decode("utf-8")
        new_tag_line = f"Tag: {python_tag}-{abi_tag}-{platform_tag}\n"
        new_wheel_lines: list[str] = []
        tag_replaced = False
        for line in wheel_text.splitlines(keepends=True):
            if line.startswith("Tag:"):
                if not tag_replaced:
                    new_wheel_lines.append(new_tag_line)
                    tag_replaced = True
            else:
                new_wheel_lines.append(line)
        if not tag_replaced:
            new_wheel_lines.append(new_tag_line)
        new_wheel_bytes = "".join(new_wheel_lines).encode("utf-8")

        old_record = src.read(record_file).decode("utf-8")
        new_record_lines: list[str] = []
        for line in old_record.splitlines():
            if not line:
                continue
            parts = line.rsplit(",", 2)
            if len(parts) != 3:
                raise ValueError(f"malformed RECORD line: {line!r}")
            path = parts[0]
            if path == wheel_file:
                new_record_lines.append(
                    f"{path},{_record_hash(new_wheel_bytes)},{len(new_wheel_bytes)}"
                )
            else:
                new_record_lines.append(line)
        new_record_bytes = ("\n".join(new_record_lines) + "\n").encode("utf-8")

        out_buf = io.BytesIO()
        with zipfile.ZipFile(out_buf, "w", zipfile.ZIP_DEFLATED) as dst:
            for info in src.infolist():
                if info.filename == wheel_file:
                    entry_data = new_wheel_bytes
                elif info.filename == record_file:
                    entry_data = new_record_bytes
                else:
                    entry_data = src.read(info.filename)
                new_info = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
                new_info.create_system = info.create_system
                new_info.create_version = info.create_version
                new_info.extract_version = info.extract_version
                new_info.flag_bits = info.flag_bits
                new_info.external_attr = info.external_attr
                new_info.internal_attr = info.internal_attr
                new_info.compress_type = info.compress_type
                dst.writestr(new_info, entry_data)

        return out_buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--platform-tag", required=True)
    parser.add_argument("--python-tag", default="py3")
    parser.add_argument("--abi-tag", default="none")
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete the source wheel after a successful retag",
    )
    args = parser.parse_args(argv)

    temp_path: Path | None = None
    out_path: Path | None = None
    try:
        if not args.wheel.is_file():
            raise FileNotFoundError(f"source wheel not found: {args.wheel}")
        for arg_name, value in (
            ("--platform-tag", args.platform_tag),
            ("--python-tag", args.python_tag),
            ("--abi-tag", args.abi_tag),
        ):
            if not _TAG_SAFE.fullmatch(value):
                raise ValueError(f"{arg_name} contains unsafe characters: {value!r}")

        m = _WHEEL_FILENAME_RE.match(args.wheel.name)
        if m is None:
            raise ValueError(f"source wheel filename does not match PEP 427: {args.wheel.name}")
        name = m.group("name")
        version = m.group("ver")

        src_bytes = args.wheel.read_bytes()
        new_bytes = _retag_wheel_bytes(
            src_bytes,
            name,
            version,
            args.python_tag,
            args.abi_tag,
            args.platform_tag,
        )
        new_name = f"{name}-{version}-{args.python_tag}-{args.abi_tag}-{args.platform_tag}.whl"
        args.outdir.mkdir(parents=True, exist_ok=True)
        out_path = args.outdir / new_name
        if out_path.exists():
            raise FileExistsError(f"destination wheel already exists: {out_path}")

        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=args.outdir,
            prefix=".retag-",
            suffix=".whl.tmp",
            delete=False,
        ) as tf:
            temp_path = Path(tf.name)
            tf.write(new_bytes)
            tf.flush()
            os.fsync(tf.fileno())
        os.replace(temp_path, out_path)
        temp_path = None

        if args.replace and args.wheel.resolve() != out_path.resolve():
            args.wheel.unlink()

    except (OSError, ValueError, FileNotFoundError, FileExistsError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    assert out_path is not None
    print(f"[OK] wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
