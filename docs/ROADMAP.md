<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# NCP Roadmap

## Phase 1: Spec + Validator (COMPLETE)

Canonical specification (v0.2.3), JSON Schemas (Draft 2020-12), 24 cross-field invariant rules, `ncp-validate` CLI, test suite with positive and negative fixtures, CI.

## Phase 2: Reference Runtime (NEXT)

Single-process runtime that loads a Graph, executes WASM Bricks, routes edges deterministically, and emits traces.

`ncp` is the Phase 2 runtime CLI (new — distinct from `ncp-validate`).

Scope:

- Load graph manifest + brick bundles
- Enforce sandbox and resource limits
- Execute WASM ABI (Section 16.2)
- Route edges per typed error and success policies (Section 7.4.1)
- Handle Result union (Success / LowConfidence / Failure)
- Emit minimal trace records (Section 11.1)

Replay (Section 12.2) is explicitly out of scope for Phase 2.

**Definition of Done:**

```
ncp run examples/graphs/support-routing/graph.yaml --input sample.json
```

## Phase 3: Integrations

- MCP server adapter: one MCP tool = one NCP graph
- LangGraph node wrapper: an NCP graph as a node in a larger workflow
- Python + TypeScript SDKs to pack bricks and run graphs

## Phase 4: Distribution + Learning

- Brick registry (or GitHub-based packs)
- Full conformance suite for third-party runtimes
- Optional adaptive routing / learned thresholds
