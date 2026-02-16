<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# NCP — Neural Computation Protocol

> **Status: Under Construction** — Spec, schemas, and validator are being finalized.
> In the meantime you can [read the spec](spec/ncp-v0.2.2.md), [open an issue](../../issues/new/choose) against any section, or watch the repo for updates.

NCP standardizes composable, auditable micro-agent primitives for agentic systems. The protocol defines:

- **Bricks** — Pure-functional, sandboxed WASM computation units
- **Graphs** — Compositions of Bricks connected by typed edges with routing policies
- **Runtime** — Executor that sandboxes Bricks, routes signals, owns state, and produces traces

## Core Philosophy

Bricks are commodity: open-source, reusable, deterministic. Graphs are product: proprietary topology plus synaptic weights equals competitive advantage. Intelligence lives in graph topology, not individual Bricks.

## Repository Structure

```
├── spec/           # Canonical protocol specification (Markdown + PDF releases)
├── schemas/        # JSON Schema (Draft 2020-12) for all NCP structures
├── examples/       # Example Brick and Graph manifests from spec appendices
├── tools/          # Validator CLI and tooling
└── .github/        # CI workflows and issue templates
```

## Specification

- **Current version:** v0.2.2
- **Canonical spec:** [spec/ncp-v0.2.2.md](spec/ncp-v0.2.2.md)
- **PDF releases:** [spec/releases/](spec/releases/)

## Quick Start

> Coming soon — the validator CLI is under development.

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
