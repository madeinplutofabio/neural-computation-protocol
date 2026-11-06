<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# NCP starter: basic-classifier

A keyword-based ticket classifier, packaged as a reusable NCP workflow.

Copy this folder, edit the `classify()` function in [`brick/src/lib.rs`](brick/src/lib.rs), and run one build command. You'll get a self-contained, versioned `.tar.gz` workflow archive that teammates can install and run with the NCP Python SDK. The same installed workflow can also be used through NCP's MCP and LangGraph integrations.

Three paths below, in order of effort:

1. [Try and customize](#try-and-customize) (one file, no identity change)
2. [Publish your own](#publish-your-own) (three identity edits, then rebuild)
3. [When you change the I/O shape](#when-you-change-the-io-shape) (schema + encoder edits)

## Prerequisites

- Rust 1.94+ with the wasm32 target: `rustup target add wasm32-unknown-unknown`
- Python 3.10+
- `pip install 'ncp-runtime[workflows]'`

Node is **not** required. The `ncp` runtime binary is **not** required at build time (only at install/run time, and it ships bundled with the SDK wheel on supported platforms).

## Try and customize

1. Open [`brick/src/lib.rs`](brick/src/lib.rs). The `classify(text: &str) -> &'static str` function is the only thing you need to change. Tweak the keywords or add rules.
2. (Optional) extend the `#[cfg(test)]` module below it to cover your new rules.
3. Run the build:

   ```bash
   bash scripts/build.sh          # Linux / macOS
   ```

   ```powershell
   pwsh scripts\build.ps1         # Windows
   ```

   The script runs `cargo test`, compiles the brick to wasm32, stamps the manifest with the WASM's SHA-256, and packages everything into `dist/basic-classifier-0.1.0.tar.gz`. It prints the archive's SHA-256 so you can distribute it.

4. Install and run your workflow locally:

   ```python
   from pathlib import Path
   import hashlib

   from ncp_runtime import NCP
   from ncp_runtime.workflows import install

   archive = Path("dist/basic-classifier-0.1.0.tar.gz")
   sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()

   install(archive, sha256=sha256)
   result = NCP().run_workflow(
       "com.example.basic-classifier",
       inputs={"text": "please refund my invoice"},
       version="0.1.0",
   )
   print(result.terminals[0].output)
   # {'category': 'billing'}
   ```

   The SHA-256 is still what you give teammates for distribution: they must pass it to `install(..., sha256=...)` so their download is verified against yours. The example above just saves you from pasting it by hand during local experimentation.

### Note on the shipped test fixtures

[`samples/valid.json`](samples/valid.json) + [`samples/invalid.json`](samples/invalid.json) and the pytest cases in [`tests/test_round_trip.py`](tests/test_round_trip.py) are a matched pair for the shipped `classify()` rules (billing / support / general). If you change the rules, update the samples and tests to match, or expect the round-trip test to detect the drift. That is intentional: the fixtures document your current contract, not a frozen set of random inputs.

## Publish your own

Only needed when you are about to distribute under your own identity. For local iteration you do NOT need any of this.

The brick's identity string `com.example.basic-classifier` appears in five places across four files:

| File | Occurrences |
|---|---|
| [`workflow/bricks/basic-classifier/manifest.template.yaml`](workflow/bricks/basic-classifier/manifest.template.yaml) | 1 (the `brick_id:` field) |
| [`workflow/graph.yaml`](workflow/graph.yaml) | 2 (`graph_id:` and the node's inline `brick_id`) |
| [`scripts/build.sh`](scripts/build.sh) | 1 (the `NAME=` line) |
| [`scripts/build.ps1`](scripts/build.ps1) | 1 (the `$NAME = ` line) |

Replace `com.example.basic-classifier` with your new identity in the manifest template, both identity fields in `graph.yaml`, and the `NAME` variable in each build script. Keep the trailing `basic-classifier` portion unchanged to avoid renaming directories or WASM artifacts (example: `com.acme.basic-classifier`).

Then rebuild (`bash scripts/build.sh` or `pwsh scripts\build.ps1`). The round-trip test reads the identity from `graph.yaml`, so it adapts automatically; you don't need to touch the test file.

## When you change the I/O shape

The starter is a working example, not a general Brick-authoring SDK. If you change what your workflow **accepts** or **returns**, you must edit matching pieces together:

If you change the **input** structure (e.g. accept extra fields, or rename `text`):

- The CBOR input decoder inside `invoke` in [`brick/src/lib.rs`](brick/src/lib.rs) (the `extract_text_field` helper under "DO NOT EDIT BELOW")
- [`workflow/bricks/basic-classifier/schemas/input.schema.json`](workflow/bricks/basic-classifier/schemas/input.schema.json)
- Your sample fixtures in [`samples/`](samples/) and the matching cases in [`tests/test_round_trip.py`](tests/test_round_trip.py)

If you change the **output** structure (e.g. return more than one field, or change the category enum):

- `classify()`'s signature/return type in [`brick/src/lib.rs`](brick/src/lib.rs)
- The CBOR output encoder in the same file (the `build_success` helper under "DO NOT EDIT BELOW")
- [`workflow/bricks/basic-classifier/schemas/output.schema.json`](workflow/bricks/basic-classifier/schemas/output.schema.json)
- Your sample fixtures in [`samples/`](samples/) and the matching assertions in [`tests/test_round_trip.py`](tests/test_round_trip.py)

## What NOT to edit

- The wasm32-only ABI block in `brick/src/lib.rs` (allocator, panic handler, `alloc`/`free`/`invoke` exports, result-buffer writer). Reused verbatim from NCP's reference brick crates.
- [`scripts/stamp_manifest.py`](scripts/stamp_manifest.py). Minimal digest+size stamper; replace it only if you want to drop the Python prereq.

## Limitations

- Deterministic in-WASM logic only. No network, filesystem, or WASI access from the brick.
- Result ≤ 64 KiB, memory ≤ 16 MiB, max_input ≤ 64 KiB (manifest-level limits).
- `max_ms: 50` in the manifest is metadata, not an enforced wall-clock deadline: CPU fuel metering is scaffolded in the current runtime but off by default.
- Archive SHA-256 establishes content integrity, not author trust. Consumers detect tampered downloads but should not treat a matching digest as an endorsement of the publisher.

## See also

- [Workflow packaging and consumption](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/examples/workflows/README.md): the producer + consumer walkthrough for NCP 0.4.0
- [Compatibility matrix](https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/COMPATIBILITY.md): which `ncp-runtime` version this starter pins
- [Starters catalog + contribution contract](../README.md)
