<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# Supported ICP Vocabulary (v0.1)

Closed set of recognized ICP descriptors used by the `ncp-icp-qualifier`
Brick in v0.1. Matching is deterministic, offline, case-insensitive, and
tokenizes `target_icp` on whitespace.

## Vocabulary

Three descriptors per dimension, nine total.

| Dimension | Descriptors |
|---|---|
| **Industry** | `b2b saas`, `manufacturing`, `retail` |
| **Segment** | `smb`, `mid-market`, `enterprise` |
| **Audience** | `b2b`, `b2c`, `creators` |

## Matching rule

Each dimension is evaluated **independently** against the normalized
token sequence of `target_icp`:

- **Multi-word descriptors** (e.g. `b2b saas`) match when their tokens
  appear contiguously in `target_icp`.
- **Single-word descriptors** (e.g. `b2b`) match when the token appears
  in any position.

**Tokens may contribute to more than one dimension.** For example,
`"b2b saas smb"` makes the `b2b saas` industry descriptor interpretable
AND the standalone `b2b` audience descriptor interpretable -- the two
dimensions are independent tests over the same token sequence.

## Dimension interpretability and short-circuit order

After validating the complete input contract, the Brick first checks
`profile_found`. If `false`, it returns `needs_enrichment` with the
unknown-domain reason, without interpreting `target_icp`. If `true`,
it evaluates ICP vocabulary coverage and returns `needs_enrichment`
when any of the three dimensions lacks a recognized descriptor.

A dimension is **interpretable** when `target_icp` contains at least
one supported descriptor from that dimension.

## Unrecognized text

Tokens outside the closed vocabulary (e.g. "selling", "to", "for") are
harmless connecting words. They do not affect scoring and do not
trigger `needs_enrichment` by themselves.

A dimension becomes uninterpretable only when **no** supported
descriptor of that dimension appears in `target_icp`.

## Scoring

Each dimension produces a binary match against the resolved profile:

- `industry_match`, `segment_match`, `audience_match`: each `0` or `1`
- `matched_dimensions = sum of the three` (integer, `0..3`)
- `score = matched_dimensions / 3` (one of `0`, `1/3`, `2/3`, `1`)

Decision rule (integer comparison):

- `matched_dimensions >= 2` → `qualified`
- `matched_dimensions <= 1` → `not_qualified`

The output schema requires `score > 0.5` for `qualified` and
`score <= 0.5` for `not_qualified`. In v0.1, this represents a strict
majority of the three equally weighted dimensions. A future weighted
implementation would instead need to document the meaning of its
weights and score.

## Out of scope for v0.1

- Weighted dimensions
- Synonyms and inflected forms (e.g. "SaaS" vs "saas" is handled by
  lowercase normalization; "software-as-a-service" is not)
- `notes` field influence on scoring (reserved for future
  deterministic rules)
- Fuzzy matching, embeddings, or any LLM-driven interpretation

## Adding a descriptor

Changing the vocabulary requires, in a single PR:

1. Update this document
2. Update the Brick's internal vocabulary table (Rust constants)
3. Rebuild the WASM artifact and update the manifest digest
4. Add at least one fixture exercising the new descriptor
5. No JSON Schema change -- descriptors are free-strings in Brick I/O
