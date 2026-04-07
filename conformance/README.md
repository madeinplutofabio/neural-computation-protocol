<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# NCP Conformance Test Vectors

Test vectors for validating NCP runtime implementations. Each vector set targets a specific normative requirement from the spec.

## Canonicalization (`canonicalization/vectors.json`)

Tests deterministic CBOR encoding per RFC 8949 §4.2 (Section 5.2).

Each vector: `{ "description", "input" (JSON value), "expected_cbor_hex" (lowercase hex string) }`.

A conformant implementation MUST produce byte-identical CBOR output for each input. Map keys are sorted by the length of their encoded key bytes, then lexicographically by those bytes.

**Important:** The hex values MUST be verified against a reference CBOR library (e.g., Python `cbor2` with `canonical=True`) before use in conformance testing.

## Routing (`routing/vectors.json`)

Tests routing evaluation order per Section 7.4.1.

Each vector: `{ "description", "edges" (outbound edge definitions), "result" (result variant + error_class), "expected_targets" (ordered list of dispatched edge_ids) }`. Optional `result_confidence` for threshold gating tests.

A conformant runtime MUST dispatch to exactly the expected targets in the expected order.

Defaults (per Section 7.4.1): if `priority` is omitted on an edge, it defaults to `0` for ordering. If `on_success.weight` is omitted, it defaults to `0.0`.

## Usage

These vectors are JSON files that can be consumed by any test framework. Load the file, iterate vectors, and assert your implementation matches expected outputs.
