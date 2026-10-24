<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# Lead-Qualification Example (issue #29)

A deterministic, offline, two-Brick workflow that composes a fictional
company-profile lookup with ICP qualification. **No LLM, no network,
no external service.** The example demonstrates repeatable
deterministic graph execution: same input + same pinned artifacts
produce the same business outcome every time.

## What this demonstrates

```
   { company_url, target_icp, notes? }
                │
                ▼
         ┌──────────────┐
         │    lookup    │   company-profile-lookup Brick
         │              │   normalizes URL + 3-entry fictional catalog
         └──────┬───────┘
                │  on_success: 7-field edge mapping
                │  (excludes company_url_normalized)
                ▼
         ┌──────────────┐
         │   qualify    │   icp-qualifier Brick
         │              │   closed 9-descriptor vocabulary
         └──────┬───────┘
                │
                ▼
   terminal: Success(qualified | not_qualified | needs_enrichment)
             or Failure(INVALID_INPUT) if lookup rejects input
```

Both Bricks ship as pinned WASM artifacts whose SHA-256 digests the
runtime verifies on every load. If either artifact's bytes drift from
its manifest-declared digest, the graph refuses to execute.

## Try it

The two commands below assume the repo was built with
`cargo build --release` (which produces `target/release/ncp`). Replace
with `cargo install --path runtime` + `ncp` if you prefer the
globally-installed CLI.

### 1. A qualified lead

```bash
./target/release/ncp run \
  examples/graphs/lead-qualification/graph.yaml \
  --brick-dir examples/bricks \
  --input examples/graphs/lead-qualification/samples/qualified_3_of_3.json
```

Expected business output (abridged — the CLI also emits a JSONL
trace to stderr):

```json
{
  "company_name": "Example SaaS",
  "icp_match": true,
  "industry": "B2B SaaS",
  "next_action": "qualified",
  "reasons": [
    "Industry matches: b2b saas",
    "Segment matches: smb",
    "Audience matches: creators (target recognizes b2b, creators)"
  ],
  "score": 1.0
}
```

Exit code: `0`. Both Bricks executed; terminal is `qualify` with
`icp_match: true` and a bit-exact `score` of `1.0`.

### 2. A failure at lookup — qualifier is never invoked

```bash
./target/release/ncp run \
  examples/graphs/lead-qualification/graph.yaml \
  --brick-dir examples/bricks \
  --input examples/graphs/lead-qualification/samples/failure_bad_type.json
```

Expected business output:

```json
{
  "error": {
    "error_class": "INVALID_INPUT",
    "message": "company_url: must be a CBOR text string"
  }
}
```

Exit code: `1`. The trace shows a single invocation at `lookup` and no
dispatch to `qualify` — the graph's `on_success` routing prevents a
downstream business decision from a failed upstream Brick.

## Three business outcomes vs one invalid-input failure

| Outcome | Meaning | NCP result type |
|---|---|---|
| `qualified` | Profile resolved AND >= 2 of 3 ICP dimensions match | Success |
| `not_qualified` | Profile resolved AND <= 1 of 3 ICP dimensions match (including zero-match) | Success |
| `needs_enrichment` | Profile not in catalog OR target_icp lacks recognized vocabulary across all 3 dimensions | Success |
| INVALID_INPUT | lookup rejected the input (missing field, wrong type, unparseable URL) | Failure |

**All three business outcomes are NCP `Success`.** `needs_enrichment`
is a completed business decision — "we cannot score this with the
current evidence" — not an execution failure. Only genuinely malformed
input (wrong types, missing required fields) produces NCP `Failure`.

## The 11 fixtures

`samples/` contains 11 reproducible test inputs covering every outcome
of the graph:

| Fixture | Terminal |
|---|---|
| `qualified_3_of_3.json` | Success / qualified / score 1.0 |
| `qualified_2_of_3.json` | Success / qualified / score 2/3 |
| `not_qualified_1_of_3.json` | Success / not_qualified / score 1/3 |
| `not_qualified_0_of_3.json` | Success / not_qualified / score 0.0 (not needs_enrichment) |
| `needs_enrichment_unknown.json` | Success / needs_enrichment (profile not in catalog) |
| `needs_enrichment_unsupported_icp.json` | Success / needs_enrichment (ICP has no recognized vocabulary) |
| `needs_enrichment_partial_icp.json` | Success / needs_enrichment (one dimension uninterpretable) |
| `failure_missing_url.json` | Failure / INVALID_INPUT at lookup |
| `failure_bad_type.json` | Failure / INVALID_INPUT at lookup |
| `failure_malformed_url.json` | Failure / INVALID_INPUT at lookup |
| `qualified_3_of_3_with_notes.json` | Byte-identical business output to `qualified_3_of_3.json` (notes propagates but does not affect qualification) |

## What qualifies as a "match"

The qualifier uses a closed 9-descriptor vocabulary across three
dimensions (industry / segment / audience). See
[`examples/bricks/icp-qualifier/SUPPORTED_ICP_VOCABULARY.md`](../../bricks/icp-qualifier/SUPPORTED_ICP_VOCABULARY.md)
for the canonical list, matching rules, and the strict-majority
threshold.

## Determinism — what this example DOES and DOES NOT claim

The example provides **repeatable deterministic graph execution**:
identical input bytes + pinned artifacts produce identical business
outputs (next_action, icp_match, bit-exact score, reasons, error
class, error message). This is verified by
`runtime/tests/graph_lead_qualification.rs`.

It is **not** a cryptographic replay certificate. The JSONL trace
contains timestamps, latency measurements, and generated session/trace
IDs (unless pinned via `ExecuteOptions`); those vary across runs by
design and are not part of the determinism contract. The determinism
guarantee covers business-output fields only.

## Repeatable replay check

For a one-command demonstration that the deterministic business
contract holds across repeat invocations, run:

```bash
./examples/graphs/lead-qualification/replay-demo.sh
```

The script invokes three representative fixtures twice each
(`qualified_2_of_3.json`, `not_qualified_0_of_3.json`,
`failure_bad_type.json`), projects each run's output to the stable
business fields (excluding timestamps, latency, and generated trace
IDs), and confirms the two projections match. Exit code 0 on success,
1 on any mismatch or unexpected CLI status.

Requires: `bash`, `jq`, and a pre-built `ncp` binary at
`./target/release/ncp` (override via `NCP_BIN=/path/to/ncp`).

## See also

- [`graph.yaml`](graph.yaml) — the 2-node graph manifest with the
  7-field edge mapping (excludes `company_url_normalized`)
- [`samples/`](samples/) — the 11 reproducible fixtures
- [`replay-demo.sh`](replay-demo.sh) — one-command repeatability check
- [`../../bricks/company-profile-lookup/manifest.yaml`](../../bricks/company-profile-lookup/manifest.yaml)
- [`../../bricks/icp-qualifier/manifest.yaml`](../../bricks/icp-qualifier/manifest.yaml)
- [`../../bricks/icp-qualifier/SUPPORTED_ICP_VOCABULARY.md`](../../bricks/icp-qualifier/SUPPORTED_ICP_VOCABULARY.md)
- Integration test suite: `runtime/tests/graph_lead_qualification.rs`
  (16 tests covering all 11 fixtures, graph structure, determinism,
  brick-digest integrity rejection, and edge-mapping contract
  enforcement)
