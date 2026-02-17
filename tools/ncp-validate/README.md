<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# ncp-validate

> **NCP v0.2.x** — compatible with NCP specification v0.2.0 through v0.2.3.

Structural (JSON Schema) and cross-field invariant validation for
NCP Brick and Graph manifests.

## Install

```bash
npm install        # from the repository root, or:
cd tools/ncp-validate && npm install
```

## CLI

```bash
# Validate a Brick manifest
ncp-validate brick path/to/manifest.yaml

# Validate a Graph manifest
ncp-validate graph path/to/graph.yaml

# Cross-validate a Graph against its Bricks
ncp-validate cross path/to/graph.yaml --brick-dir path/to/bricks/

# List all rules
ncp-validate rules

# Filter rules by target
ncp-validate rules --target brick
ncp-validate rules --target graph
ncp-validate rules --target cross
```

### Output formats

```bash
# Human-readable (default)
ncp-validate brick manifest.yaml

# JSON (single object for brick/graph, array for cross)
ncp-validate brick manifest.yaml --format json
ncp-validate cross graph.yaml --brick-dir bricks/ --format json
```

JSON output is a single `ValidationSummary` object for the `brick` and
`graph` commands. The `cross` command emits an array of summaries
(graph + each brick + cross).

### Cross validation phases

The `cross` command runs three phases:

1. **Graph validation** — schema + graph invariants. Stops on schema failure.
2. **Brick validation** — schema + brick invariants for every brick under `--brick-dir`. Stops on any brick schema failure.
3. **Cross invariants** — slot bindings, version compatibility, consistency checks.

All phase summaries are included in the output, so the exit code reflects
the worst result across all phases.

## Programmatic API

```typescript
import {
  validateBrickManifest,
  validateGraphManifest,
  validateInvocationEnvelope,
  validateResult,
  validateCrossManifests,
  loadFile,
} from "ncp-validate";

import type {
  BrickManifest,
  GraphManifest,
  ValidationSummary,
} from "ncp-validate";

// Single-manifest validation
const brick = loadFile<BrickManifest>("manifest.yaml");
const summary: ValidationSummary = validateBrickManifest(brick, "manifest.yaml");

if (!summary.valid) {
  for (const r of summary.results.filter((r) => r.status === "fail")) {
    console.error(`${r.rule}: ${r.message ?? r.description}`);
  }
}

// Cross-manifest validation
const graph = loadFile<GraphManifest>("graph.yaml");
const bricks = new Map<string, BrickManifest>();
bricks.set(brick.brick_id, brick);

const cross = validateCrossManifests({ graph, bricks }, "graph.yaml");
```

## Validation rules

### Brick rules (12)

| Rule | Section | Description |
|------|---------|-------------|
| `BRICK_CARRY_NULL_IFF_NONE` | §6.8 | carry_state_class=none ⇔ schemas.carry_state=null |
| `BRICK_CARRY_NONE_ZERO_BYTES` | §6.8 | carry_state_class=none ⇒ carry_state_max_bytes=0 |
| `BRICK_CARRY_EXISTS_POSITIVE_BYTES` | §6.8 | carry_state_class≠none ⇒ carry_state_max_bytes>0 |
| `BRICK_WALL_NOT_BIT_EXACT` | §6.7 | time_model=wall_bounded ⇒ determinism.mode≠bit_exact |
| `BRICK_DRIFT_NEEDS_EPSILON` | §6.6 | bounded_drift ⇒ epsilon present and >0 |
| `BRICK_STOCHASTIC_NEEDS_SEED` | §6.6 | stochastic ⇒ seed_source="ctx.seed" |
| `BRICK_MODEL_PIN_NEEDS_STOCHASTIC` | §6.6.1 | model_pin present ⇒ mode=stochastic |
| `BRICK_MODEL_PIN_NEEDS_REPRO_LEVEL` | §6.6.1 | model_pin present ⇒ reproducibility_level present |
| `BRICK_REPRO_LEVEL_NEEDS_MODEL_PIN` | §6.6.1 | reproducibility_level present ⇒ model_pin present |
| `BRICK_SIDE_EFFECTS_NEEDS_CARRY` | §6.9 | side_effects_schema≠null ⇒ carry_state declared |
| `BRICK_CAPABILITIES_EMPTY` | §6.5 | capabilities[] must be empty in v0.2 |
| `BRICK_FORMAT_WASM` | §6.2 | artifact.format must be "wasm" |

### Graph rules (8)

| Rule | Section | Description |
|------|---------|-------------|
| `GRAPH_NODE_IDS_UNIQUE` | §7.2 | all node_ids must be unique |
| `GRAPH_EDGE_IDS_UNIQUE` | §7.3 | all edge_ids must be unique |
| `GRAPH_EDGE_SOURCE_EXISTS` | §7.3 | every edge.source_node must reference an existing node_id |
| `GRAPH_EDGE_TARGET_EXISTS` | §7.3 | every edge.target_node must reference an existing node_id |
| `GRAPH_NO_SELF_EDGES` | §7.3 | source_node ≠ target_node for every edge |
| `GRAPH_ON_ERROR_TARGETS_EXIST` | §7.4 | every on_error target must reference an existing node_id |
| `GRAPH_QUORUM_VALID` | §7.0 | mode=quorum ⇒ 0 < quorum_n ≤ quorum_m |
| `GRAPH_PRIORITY_EDGES_HAVE_PRIORITY` | §7.3 | conflict_resolution=priority ⇒ all inbound edges have priority field |

### Cross rules (4)

| Rule | Section | Description |
|------|---------|-------------|
| `CROSS_BRICK_MANIFEST_AVAILABLE` ⚠ | §7.2 | every node's brick_id must resolve to a Brick manifest |
| `CROSS_SLOTS_FULLY_BOUND` | §7.2 | graph bindings must cover all slots declared by each Brick |
| `CROSS_SLOT_TYPES_MATCH` | §6.10 | slot bindings must be {ref, consistency?} with matching consistency |
| `CROSS_VERSION_SATISFIES` | §7.2 | version_or_range in graph must be satisfiable by Brick's version |

⚠ = warning only (does not cause exit code 1).

## Two-phase validation

Each single-manifest validator runs two phases:

1. **JSON Schema** — structural validation against the NCP schema.
2. **Invariant rules** — cross-field consistency checks (only if schema passes).

If the schema phase fails, invariant rules are skipped entirely to avoid
null/undefined surprises from malformed input.

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | All checks passed |
| `1` | One or more checks failed |
| `2` | Runtime error (file not found, invalid YAML, etc.) |

## Development

```bash
npm run build         # Compile TypeScript
npm test              # Run test suite (vitest)
npm run test:watch    # Watch mode
npm run validate-examples  # Validate example manifests from spec
```

## License

Apache-2.0
