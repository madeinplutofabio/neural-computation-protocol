#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori
#
# Starter build pipeline (Linux/macOS).
#
# Prereqs: Rust 1.94+ with wasm32-unknown-unknown target, Python 3.10+,
# and `pip install 'ncp-runtime[workflows]'`.
#
# When publishing under your own identity, change NAME below (see the
# starter README, "Publish your own").

set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

# Prefer python3; fall back to python (including on Windows/Git Bash).
# Probe execution, not just PATH presence: Windows may provide a
# nonfunctional Microsoft Store python3 alias.
PY="${PYTHON:-}"

if [ -n "$PY" ]; then
    if ! "$PY" -c "import sys; assert sys.version_info >= (3, 10)" >/dev/null 2>&1; then
        echo "error: PYTHON must point to a working Python 3.10+ interpreter: $PY" >&2
        exit 1
    fi
elif command -v python3 >/dev/null 2>&1 &&
     python3 -c "import sys; assert sys.version_info >= (3, 10)" >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1 &&
     python -c "import sys; assert sys.version_info >= (3, 10)" >/dev/null 2>&1; then
    PY=python
else
    echo "error: Python 3.10+ not found (tried python3 and python)" >&2
    exit 1
fi

NAME="com.example.basic-classifier"
VERSION="0.1.0"
ARCHIVE="dist/basic-classifier-${VERSION}.tar.gz"

echo "[1/5] Native test of classify()"
( cd brick && cargo test --locked )

echo "[2/5] Compile brick to wasm32-unknown-unknown"
( cd brick && cargo build --release --target wasm32-unknown-unknown --locked )

echo "[3/5] Copy wasm into brick-dir layout"
mkdir -p workflow/bricks/basic-classifier
cp brick/target/wasm32-unknown-unknown/release/basic_classifier.wasm \
   workflow/bricks/basic-classifier/basic-classifier.wasm

echo "[4/5] Stamp manifest with digest + size"
"$PY" scripts/stamp_manifest.py \
   workflow/bricks/basic-classifier/manifest.template.yaml \
   workflow/bricks/basic-classifier/basic-classifier.wasm \
   workflow/bricks/basic-classifier/manifest.yaml

echo "[5/5] Package the workflow"
mkdir -p dist
"$PY" -m ncp_runtime.workflows package \
   --graph workflow/graph.yaml \
   --brick-dir workflow/bricks \
   --name "$NAME" \
   --version "$VERSION" \
   --runtime-compat ">=0.4.0,<0.5.0" \
   --out "$ARCHIVE"

SHA=$("$PY" -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$ARCHIVE")

cat <<EOF

NCP starter build successful.

  Brick:     $NAME
  Version:   $VERSION
  Workflow:  $ARCHIVE
  SHA-256:   $SHA

Your workflow is ready to install.

Next: see README.md -> "Install and run".
EOF
