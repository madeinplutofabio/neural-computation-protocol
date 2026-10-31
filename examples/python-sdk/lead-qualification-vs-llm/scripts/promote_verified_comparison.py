# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori
"""Promote a verified `verification/all-50-combined-clean.json` to
`published/comparison.json`.

Procedure:
  1. Refuse if published/comparison.json already exists (checked at the
     start AND rechecked immediately before the atomic replace). This is a
     first-publication safeguard, not a race-proof no-clobber guarantee.
  2. Load and parse the source artifact under controlled error handling.
  3. Revalidate it with compare._validate_publication_ready(). Any
     violation aborts with a non-zero exit; nothing is written.
  4. Compute the source SHA-256.
  5. Copy the source bytes-for-bytes into a temp file *inside* published/
     (same filesystem as destination -- precondition for os.replace() to
     be atomic on all platforms). Flush + fsync before closing.
  6. Verify the temp file's SHA-256 against the source SHA-256.
  7. Recheck destination still absent, then atomically promote via
     os.replace(temp, DESTINATION).
  8. On ANY failure, delete the temp file if it exists; leave any prior
     published/comparison.json untouched.

Deliberately standalone from compare.py's --publish path: --publish
performs a fresh comparison, while this script promotes an already-
reviewed artifact without re-running the API.

Invocation (from the example directory):
    python scripts/promote_verified_comparison.py
    python scripts/promote_verified_comparison.py --source other.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

EXAMPLE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EXAMPLE_DIR))

import compare  # noqa: E402

DEFAULT_SOURCE = EXAMPLE_DIR / "verification" / "all-50-combined-clean.json"
PUBLISHED_DIR = EXAMPLE_DIR / "published"
DESTINATION = PUBLISHED_DIR / "comparison.json"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help=f"Verified comparison JSON to promote (default: {DEFAULT_SOURCE})",
    )
    args = parser.parse_args()
    source: Path = args.source.resolve()

    if not source.is_file():
        print(f"ERROR: source does not exist: {source}", file=sys.stderr)
        return 1

    print(f"source      = {source}")
    print(f"destination = {DESTINATION}")

    # Step 1a: early no-overwrite check. First publication only; refuses
    # any pre-existing published artifact rather than overwriting it.
    if DESTINATION.exists():
        print(
            f"ERROR: published artifact already exists; "
            f"refusing to overwrite: {DESTINATION}",
            file=sys.stderr,
        )
        return 1

    try:
        PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"ERROR: failed to create published/ directory: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    temp_path: Path | None = None
    try:
        # Step 2: load + parse under controlled error handling.
        try:
            with open(source, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"ERROR: failed to read/parse source JSON: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        # Step 3: revalidate.
        try:
            compare._validate_publication_ready(doc)
        except Exception as exc:
            print("ERROR: _validate_publication_ready() refused the artifact:",
                  file=sys.stderr)
            print(f"  {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print("[OK] _validate_publication_ready() accepted")

        # Step 4: compute source SHA-256 under controlled error handling.
        try:
            src_sha = _sha256(source)
        except OSError as exc:
            print(f"ERROR: failed to hash source: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        # Step 5: byte-for-byte copy into a same-filesystem temp file.
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".comparison-",
                suffix=".tmp",
                dir=PUBLISHED_DIR,
                delete=False,
            ) as tf:
                temp_path = Path(tf.name)
                with open(source, "rb") as src_f:
                    shutil.copyfileobj(src_f, tf)
                tf.flush()
                os.fsync(tf.fileno())
        except OSError as exc:
            print(f"ERROR: failed to write temp file: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        # Step 6: verify temp bytes match source bytes.
        try:
            tmp_sha = _sha256(temp_path)
        except OSError as exc:
            print(f"ERROR: failed to hash temp file: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        if tmp_sha != src_sha:
            print("ERROR: temp file SHA mismatch after copy", file=sys.stderr)
            print(f"  source sha256 = {src_sha}", file=sys.stderr)
            print(f"  temp   sha256 = {tmp_sha}", file=sys.stderr)
            return 1

        # Step 7: recheck no-overwrite immediately before the atomic
        # replace, then promote.
        if DESTINATION.exists():
            print(
                f"ERROR: published artifact appeared during this run; "
                f"refusing to overwrite: {DESTINATION}",
                file=sys.stderr,
            )
            return 1
        try:
            os.replace(temp_path, DESTINATION)
        except OSError as exc:
            print(f"ERROR: atomic replace failed: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        temp_path = None  # consumed by os.replace; nothing to clean up

        print(f"[OK] copied, SHA-256 verified: {src_sha}")
        print(f"[OK] wrote: {DESTINATION}")
        return 0

    finally:
        # Step 8: clean up the temp file on any failure path. Never
        # touches DESTINATION, so a prior published artifact is preserved
        # whenever this script aborts.
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
