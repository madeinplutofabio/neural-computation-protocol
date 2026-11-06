# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori
#
# Starter build pipeline (Windows).
#
# Prereqs: Rust 1.94+ with wasm32-unknown-unknown target, Python 3.10+,
# and `pip install 'ncp-runtime[workflows]'`.
#
# When publishing under your own identity, change $NAME below (see the
# starter README, "Publish your own").

$ErrorActionPreference = "Stop"

# $ErrorActionPreference stops on cmdlet errors only. Native executables
# (cargo, python) do NOT throw on nonzero exit under PowerShell, so we
# must check $LASTEXITCODE after each one. Without this, a failed cargo
# test could be followed by a wasm compile and a "success" summary.
function Assert-NativeSuccess {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed (exit code $LASTEXITCODE)"
    }
}

$here = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $here

$NAME = "com.example.basic-classifier"
$VERSION = "0.1.0"
$ARCHIVE = "dist/basic-classifier-$VERSION.tar.gz"

Write-Host "[1/5] Native test of classify()"
Push-Location brick
try {
    cargo test --locked
    Assert-NativeSuccess "Native tests"
} finally {
    Pop-Location
}

Write-Host "[2/5] Compile brick to wasm32-unknown-unknown"
Push-Location brick
try {
    cargo build --release --target wasm32-unknown-unknown --locked
    Assert-NativeSuccess "WASM compilation"
} finally {
    Pop-Location
}

Write-Host "[3/5] Copy wasm into brick-dir layout"
New-Item -ItemType Directory -Force -Path "workflow/bricks/basic-classifier" | Out-Null
Copy-Item -Force `
  "brick/target/wasm32-unknown-unknown/release/basic_classifier.wasm" `
  "workflow/bricks/basic-classifier/basic-classifier.wasm"

Write-Host "[4/5] Stamp manifest with digest + size"
python scripts/stamp_manifest.py `
  "workflow/bricks/basic-classifier/manifest.template.yaml" `
  "workflow/bricks/basic-classifier/basic-classifier.wasm" `
  "workflow/bricks/basic-classifier/manifest.yaml"
Assert-NativeSuccess "Manifest stamping"

Write-Host "[5/5] Package the workflow"
New-Item -ItemType Directory -Force -Path "dist" | Out-Null
python -m ncp_runtime.workflows package `
  --graph "workflow/graph.yaml" `
  --brick-dir "workflow/bricks" `
  --name "$NAME" `
  --version "$VERSION" `
  --runtime-compat ">=0.4.0,<0.5.0" `
  --out "$ARCHIVE"
Assert-NativeSuccess "Workflow packaging"

$SHA = (Get-FileHash -Algorithm SHA256 $ARCHIVE).Hash.ToLower()

Write-Host ""
Write-Host "NCP starter build successful."
Write-Host ""
Write-Host "  Brick:     $NAME"
Write-Host "  Version:   $VERSION"
Write-Host "  Workflow:  $ARCHIVE"
Write-Host "  SHA-256:   $SHA"
Write-Host ""
Write-Host "Your workflow is ready to install."
Write-Host ""
Write-Host "Next: see README.md -> ""Install and run""."
