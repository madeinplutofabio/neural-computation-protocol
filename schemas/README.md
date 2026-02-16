<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# NCP JSON Schemas

JSON Schema definitions for NCP v0.2.3 manifest structures, using [JSON Schema Draft 2020-12](https://json-schema.org/draft/2020-12/schema).

## Schemas

| Schema | Spec Sections | Description |
|---|---|---|
| [`brick-manifest.schema.json`](brick-manifest.schema.json) | §6.1–6.10 | Brick Manifest — identity, artifact, schemas, limits, determinism, time model, carry state, graph ref slots |
| [`graph-manifest.schema.json`](graph-manifest.schema.json) | §7.0–7.5 | Graph Manifest — nodes, edges, activation policies, routing, budgets |
| [`invocation-envelope.schema.json`](invocation-envelope.schema.json) | §8 | Invocation Envelope — context, trigger provenance, time injection, carry state delivery |
| [`result.schema.json`](result.schema.json) | §9.1–9.5 | Result Model — three-variant discriminated union (Success, LowConfidence, Failure) |

## How to use in this repo

The recommended way to validate NCP manifests is with the **validator CLI** at [`tools/ncp-validate`](../tools/ncp-validate/). The CLI performs both schema validation and cross-field invariant checks.

These schemas are also usable directly with any JSON Schema Draft 2020-12 compliant validator (e.g., AJV).

## Usage with AJV (TypeScript/JavaScript)

These schemas use Draft 2020-12 features (`if`/`then`/`else`, `$defs`, `$ref`). You **must** use the AJV 2020 import:

```ts
import fs from "node:fs";
import yaml from "yaml";
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";

const ajv = new Ajv2020({ allErrors: true });
addFormats(ajv);

const schema = JSON.parse(fs.readFileSync("brick-manifest.schema.json", "utf-8"));
const validate = ajv.compile(schema);

const manifest = yaml.parse(fs.readFileSync("manifest.yaml", "utf-8"));
if (validate(manifest)) {
  console.log("Schema validation passed");
} else {
  console.error(validate.errors);
}
```

> **Pitfall:** Using the default AJV import without enabling 2020-12 support can silently validate under older semantics. Always use `ajv/dist/2020` for these schemas. Conditional schemas (`if`/`then`/`else`) will appear to validate but won't enforce conditions, producing false positives.

### Validating with other tools

Any JSON Schema Draft 2020-12 compliant validator will work. Ensure your tool supports:

- `if` / `then` / `else` conditional schemas
- `$defs` and `$ref`
- `oneOf` with `const` discriminators
- `format: "date-time"` (for invocation envelope `wall_time`)

## What the schemas enforce vs. what requires the CLI validator

### Enforced by schemas (structural)

| Rule | Schema | Spec |
|---|---|---|
| `bounded_drift` requires `epsilon` | `brick-manifest` | §6.6 |
| `stochastic` requires `seed_source = ctx.seed` | `brick-manifest` | §6.6 |
| `wall_bounded` ⇒ determinism ≠ `bit_exact` | `brick-manifest` | §6.7 |
| `schemas.carry_state = null` ⇔ `carry_state_class = none` | `brick-manifest` | §6.8 |
| `carry_state_class = none` ⇒ `carry_state_max_bytes = 0` | `brick-manifest` | §6.8 |
| `reproducibility_level` ⇒ `mode = stochastic` | `brick-manifest` | §6.6.1 |
| `model_pin` ⇔ `reproducibility_level` | `brick-manifest` | §6.6.1 |
| Irrelevant determinism fields forbidden | `brick-manifest` | §6.6 |
| `capabilities` must be empty (v0.2) | `brick-manifest` | §6.5 |
| `artifact.format` must be `wasm` (v0.2) | `brick-manifest` | §6.2 |
| `quorum` mode requires `quorum_n` and `quorum_m` | `graph-manifest` | §7.0 |
| `quorum_n`/`quorum_m` forbidden when mode ≠ `quorum` | `graph-manifest` | §7.0 |
| `activation` requires `mode` if present | `graph-manifest` | §7.0 |
| Inline/handle carry state mutual exclusion | `invocation-envelope` | §8.2 |
| `carry_state_working_set` ⇒ `carry_state_handle` | `invocation-envelope` | §8.2 |
| `__root__` sentinel coupling (node ⇔ edge) | `invocation-envelope` | §8.1 |
| Result union shape (Success/LowConfidence/Failure) | `result` | §9.1–9.2 |
| `LowConfidence.error_class = LOW_CONFIDENCE` | `result` | §9.2 |
| `Failure.error_class ≠ LOW_CONFIDENCE` | `result` | §9.2 |
| `LowConfidence.output` includes `confidence` ∈ [0,1] | `result` | §9.5 |

### Requires CLI validator (cross-field / cross-manifest)

| Rule | Rule ID | Spec |
|---|---|---|
| `carry_state_class ≠ none` ⇒ `carry_state_max_bytes > 0` | `BRICK_CARRY_EXISTS_POSITIVE_BYTES` | §6.8 |
| `side_effects_schema ≠ null` ⇒ `carry_state ≠ null` | `BRICK_SIDE_EFFECTS_NEEDS_CARRY` | §6.9 |
| `model_pin` ⇒ `mode = stochastic` | `BRICK_MODEL_PIN_NEEDS_STOCHASTIC` | §6.6.1 |
| `quorum_n ≤ quorum_m` | `GRAPH_QUORUM_VALID` | §7.0 |
| `conflict_resolution = priority` ⇒ inbound edges have `priority` | `GRAPH_PRIORITY_EDGES_HAVE_PRIORITY` | §7.0/§7.3 |
| Edge `target_node` / `source_node` must exist | `GRAPH_EDGE_TARGET_EXISTS` / `GRAPH_EDGE_SOURCE_EXISTS` | §7.3 |
| `on_error` targets must exist | `GRAPH_ON_ERROR_TARGETS_EXIST` | §7.4 |
| Node IDs unique | `GRAPH_NODE_IDS_UNIQUE` | §7.2 |
| Edge IDs unique | `GRAPH_EDGE_IDS_UNIQUE` | §7.3 |
| No self-edges | `GRAPH_NO_SELF_EDGES` | §7.3 |
| Graph bindings cover all Brick slots | `CROSS_SLOTS_FULLY_BOUND` | §7.2/§6.10 |
| Bound slot types match Brick declarations | `CROSS_SLOT_TYPES_MATCH` | §7.2/§6.10 |
| `version_or_range` satisfiable by Brick version | `CROSS_VERSION_SATISFIES` | §7.2/§6.1 |

## Protocol Version

These schemas target **NCP v0.2.3**. See the [canonical spec](../spec/ncp-v0.2.3.md) for the full protocol definition.
