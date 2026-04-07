#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Build NCP echo brick (and optionally trap brick) to WASM.

set -euo pipefail

cd "$(dirname "$0")"

echo "Building echo.wasm..."
cargo build --target wasm32-unknown-unknown --release
cp target/wasm32-unknown-unknown/release/ncp_echo.wasm echo.wasm
echo "  → echo.wasm ($(wc -c < echo.wasm) bytes)"

echo "Building trap.wasm..."
cargo build --target wasm32-unknown-unknown --release --features trap
cp target/wasm32-unknown-unknown/release/ncp_echo.wasm trap.wasm
echo "  → trap.wasm ($(wc -c < trap.wasm) bytes)"

echo "Done."
