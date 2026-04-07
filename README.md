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

## Quick start (validator)

```bash
git clone https://github.com/madeinplutofabio/neural-computation-protocol.git
cd neural-computation-protocol/tools/ncp-validate
npm install && npm run build

# Validate a Brick manifest
npx ncp-validate brick ../../examples/bricks/sentiment-gate/manifest.yaml
# → 12 rules checked: 12 passed, 0 failed ✓

# Validate a Graph manifest
npx ncp-validate graph ../../examples/graphs/support-routing/graph.yaml
# → 8 rules checked: 8 passed, 0 failed ✓

# Cross-validate Graph against its Bricks
# Note: --brick-dir is scanned one level deep for subdirectories containing manifest.yaml|manifest.yml
npx ncp-validate cross ../../examples/graphs/support-routing/graph.yaml \
  --brick-dir ../../examples/bricks/
# → 4 rules checked: 4 passed, 0 failed ✓

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
├── examples/       # Example Brick and Graph manifests from spec appendices
├── bricks/         # Reference Brick implementations (Rust → WASM)
├── tools/          # Validator CLI and tooling
├── conformance/    # Test vectors for runtime implementors
├── docs/           # Roadmap and supplementary documentation
└── .github/        # CI workflows and issue templates
```

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
