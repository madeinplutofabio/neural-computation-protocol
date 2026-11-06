#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori
"""Stamp the WASM digest and byte size into a Brick manifest template.

Only the artifact's `digest:` and `size_bytes:` YAML fields are replaced.
No YAML parsing, no validation. The SDK's workflows.package() re-verifies
the digest matches the on-disk WASM, so a wrong stamp cannot produce a
valid archive (NCPWorkflowDigestError).

Usage:
    python stamp_manifest.py <template.yaml> <brick.wasm> <output.yaml>
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

# Anchored to the start of a YAML field line: optional leading whitespace,
# the field name + colon, optional whitespace, then the placeholder value.
# Comments (which start with `#`) cannot match because the leading
# character class does not include `#`.
_DIGEST_RE = re.compile(
    r"^([ \t]*digest:[ \t]*)sha256:PLACEHOLDER[ \t]*$",
    re.MULTILINE,
)
_SIZE_RE = re.compile(
    r"^([ \t]*size_bytes:[ \t]*)0[ \t]*$",
    re.MULTILINE,
)


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "Usage: stamp_manifest.py <template.yaml> <brick.wasm> <output.yaml>",
            file=sys.stderr,
        )
        return 2
    template, wasm, output = (Path(p) for p in sys.argv[1:4])

    blob = wasm.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    size = len(blob)

    text = template.read_text(encoding="utf-8")

    text, digest_count = _DIGEST_RE.subn(rf"\1sha256:{digest}", text)
    if digest_count != 1:
        print(
            f"stamp_manifest: expected exactly one `digest: sha256:PLACEHOLDER` "
            f"YAML field in {template}, found {digest_count}",
            file=sys.stderr,
        )
        return 1

    text, size_count = _SIZE_RE.subn(rf"\g<1>{size}", text)
    if size_count != 1:
        print(
            f"stamp_manifest: expected exactly one `size_bytes: 0` YAML field "
            f"in {template}, found {size_count}",
            file=sys.stderr,
        )
        return 1

    output.write_text(text, encoding="utf-8")
    print(f"stamped {output.name}: sha256:{digest} ({size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
