#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori
"""Rewrite the starter's brick identity namespace in the three sites a
publisher edits by hand.

This script is a CI acceptance-test helper AND documentation: it names
exactly which files + lines change when you publish the starter under
your own identity. A real developer does these three edits in their
editor; they do not run this script.

Usage:
    python tests/copy_rename.py <new-full-brick-id>

Example:
    python tests/copy_rename.py com.acme.basic-classifier

Rules:
  - Only the NAMESPACE is changed. The short name `basic-classifier`
    stays (so no file or directory renames are required).
  - Each pattern must match EXACTLY once; any mismatch aborts with no
    files modified (prevalidated batch rewrite).
  - Patterns are field-anchored so documentation comments that mention
    the old ID are never rewritten.

Note on 'atomic': this script prevalidates every match before writing,
so a validation failure leaves all files unchanged. It is NOT a
filesystem-atomic operation: a disk write failure on file 3 of 4 would
still leave files 1 and 2 changed. For a CI helper this is sufficient.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

OLD_ID = "com.example.basic-classifier"


@dataclass
class Site:
    rel_path: str
    patterns: list[tuple[re.Pattern[str], str]]  # (pattern, replacement-template)


SITES: list[Site] = [
    # 1. manifest template: `brick_id: <ID>` at line start
    Site(
        "workflow/bricks/basic-classifier/manifest.template.yaml",
        [(
            re.compile(rf"^(brick_id:\s*){re.escape(OLD_ID)}\s*$", re.MULTILINE),
            r"\g<1>{new}",
        )],
    ),
    # 2. graph.yaml: TWO specific identity fields.
    #    (a) top-level `graph_id:` at line start.
    #    (b) the node's inline `brick: { brick_id: <ID>, ... }` mapping.
    #    Both patterns are line/field anchored so a documentation comment
    #    mentioning `brick_id: com.example...` cannot match.
    Site(
        "workflow/graph.yaml",
        [
            (
                re.compile(rf"^(graph_id:\s*){re.escape(OLD_ID)}\s*$", re.MULTILINE),
                r"\g<1>{new}",
            ),
            (
                re.compile(
                    rf"^([ \t]*brick:[ \t]*\{{[ \t]*brick_id:[ \t]*)"
                    rf"{re.escape(OLD_ID)}([ \t]*,)",
                    re.MULTILINE,
                ),
                r"\g<1>{new}\g<2>",
            ),
        ],
    ),
    # 3. build.sh: `NAME="<ID>"` line
    Site(
        "scripts/build.sh",
        [(
            re.compile(rf'^NAME="{re.escape(OLD_ID)}"\s*$', re.MULTILINE),
            'NAME="{new}"',
        )],
    ),
    # 4. build.ps1: `$NAME = "<ID>"` line
    Site(
        "scripts/build.ps1",
        [(
            re.compile(rf'^\$NAME\s*=\s*"{re.escape(OLD_ID)}"\s*$', re.MULTILINE),
            '$NAME = "{new}"',
        )],
    ),
]


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    new_id = sys.argv[1]
    if "." not in new_id or new_id == OLD_ID:
        print(f"copy_rename: refusing invalid new id: {new_id!r}", file=sys.stderr)
        return 1
    if not new_id.endswith(".basic-classifier"):
        print(
            "copy_rename: new id must end with `.basic-classifier` "
            "(namespace-only rename; see starter README).",
            file=sys.stderr,
        )
        return 1

    starter_root = Path(__file__).resolve().parent.parent

    # Prevalidation pass: compute all rewrites, assert EACH PATTERN matches
    # exactly once. If anything fails, no files are modified.
    staged: list[tuple[Path, str]] = []
    errors: list[str] = []
    for site in SITES:
        path = starter_root / site.rel_path
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            errors.append(f"missing file: {site.rel_path}")
            continue
        new_text = text
        site_ok = True
        for pattern, template in site.patterns:
            replacement = template.format(new=new_id)
            new_text, count = pattern.subn(replacement, new_text)
            if count != 1:
                errors.append(
                    f"{site.rel_path}: expected exactly one match for "
                    f"{pattern.pattern!r}, got {count}"
                )
                site_ok = False
        if site_ok:
            staged.append((path, new_text))

    if errors:
        print("copy_rename: aborting with no files modified:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    # Write pass: all validation already passed above.
    for path, new_text in staged:
        path.write_text(new_text, encoding="utf-8")
        print(f"rewrote {path.relative_to(starter_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
