<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

<p align="center">
  <img src="NCP-logo.png" alt="NCP Logo" width="200">
</p>

# NCP — Neural Computation Protocol

[![Validate](https://github.com/madeinplutofabio/neural-computation-protocol/actions/workflows/validate.yml/badge.svg)](https://github.com/madeinplutofabio/neural-computation-protocol/actions/workflows/validate.yml)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

NCP standardizes composable, auditable micro-agent primitives for agentic systems. The protocol defines:

- **Bricks** — pure-functional, sandboxed WASM computation units
- **Graphs** — compositions of Bricks connected by typed edges with routing policies
- **Runtime** — executor that sandboxes Bricks, routes signals, owns state, and produces traces

## Core philosophy

Bricks are commodity: open-source, reusable, deterministic. Graphs are product: proprietary topology plus synaptic weights equals competitive advantage. Intelligence lives in graph topology, not individual Bricks.

## Quick start (runtime)

```bash
# Build the reference runtime (requires Rust 1.94+)
cargo build --release

# Run a single-node graph
cargo run -- run examples/graphs/echo-pipeline/graph.yaml \
  --input examples/graphs/echo-pipeline/sample.json

# Run a two-node chain with routing and field mapping
cargo run -- run examples/graphs/echo-chain/graph.yaml \
  --input examples/graphs/echo-chain/sample.json

# Run with safety budget
cargo run -- run examples/graphs/echo-chain/graph.yaml \
  --input examples/graphs/echo-chain/sample.json --max-steps 1

# Write trace to file instead of stderr
cargo run -- run examples/graphs/echo-pipeline/graph.yaml \
  --input examples/graphs/echo-pipeline/sample.json --trace trace.jsonl

# Output all terminal results as JSON array
cargo run -- run examples/graphs/echo-chain/graph.yaml \
  --input examples/graphs/echo-chain/sample.json --all-terminals
```

## Quick start (validator)

```bash
cd tools/ncp-validate
npm install && npm run build

# Validate a Brick manifest
npx ncp-validate brick ../../examples/bricks/sentiment-gate/manifest.yaml
# → 12 rules checked: 12 passed, 0 failed

# Validate a Graph manifest
npx ncp-validate graph ../../examples/graphs/support-routing/graph.yaml
# → 8 rules checked: 8 passed, 0 failed

# Cross-validate Graph against its Bricks
npx ncp-validate cross ../../examples/graphs/support-routing/graph.yaml \
  --brick-dir ../../examples/bricks/
# → 4 rules checked: 4 passed, 0 failed

# List all validation rules
npx ncp-validate rules
```

## Specification

- **Current version:** v0.2.3
- **Canonical spec:** [spec/ncp-v0.2.3.md](spec/ncp-v0.2.3.md)
- **PDF releases:** [spec/releases/](spec/releases/)

## Repository structure

```
├── spec/           # Canonical protocol specification (Markdown + PDF releases)
├── schemas/        # JSON Schema (Draft 2020-12) for all NCP structures
├── runtime/        # Reference runtime (Rust, Wasmtime 43)
├── bricks/         # Reference Brick implementations (Rust → WASM)
├── examples/       # Example Brick manifests, Graph manifests, and test fixtures
├── tools/          # Validator CLI and tooling
├── conformance/    # Test vectors for runtime implementors
├── docs/           # Roadmap and supplementary documentation
└── .github/        # CI workflows and issue templates
```

## Runtime

The `ncp-runtime` reference runtime (`runtime/`) loads a Graph manifest, resolves and verifies WASM Brick bundles, and executes the graph via FIFO queue orchestration:

- WASM sandbox via Wasmtime 43 with memory limits
- Model B ABI: `alloc`/`free`/`invoke` with 4-byte LE length-prefixed results
- CBOR envelope with sorted keys for cross-runtime determinism
- Result boundary validation (Success / LowConfidence / Failure)
- Deterministic routing per Section 7.4.1 (on_error priority, on_success weight/threshold)
- Field mapping with dot-path resolution and deep merge
- SHA-256 artifact digest verification
- JSON Lines trace emission (Section 11.1)
- Safety budgets: `--max-steps`, `--max-queued`

See `cargo run -- run --help` for full CLI options.

## Validator

The `ncp-validate` CLI validates manifests in two phases:

1. **JSON Schema** — structural validation against NCP Draft 2020-12 schemas
2. **Invariant rules** — cross-field consistency checks derived from the spec

See [tools/ncp-validate/README.md](tools/ncp-validate/README.md) for full CLI docs and the complete rule list.

## Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md) for the phased development plan.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
