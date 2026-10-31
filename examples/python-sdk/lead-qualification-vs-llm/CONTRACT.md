<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Fabio Marcello Salvadori
-->

# Evaluation Contract — `lead-qualification-vs-llm`

**Status:** v0.1 — frozen for PR 3A.4-3. Any change to this document before PR 3 merges requires explicit review and rerunning affected tests (§14). After merge, substantive changes use normal follow-up PRs.

**Fixture-set revision:** `fixtures-rev-01` (initial frozen set; see §7.1).

**Last live-API verification:** _pending; required before merge (see §13)._

## 1. Scope

This document is the single source of truth for a **measured side-by-side example** comparing a deterministic NCP graph against a current-generation LLM baseline on the same lead-qualification task. The example lives at `examples/python-sdk/lead-qualification-vs-llm/`.

### 1.1 What this example IS

- A reproducible comparison of **correctness, model-API calls, provider-reported token usage, cost, latency, and execution status** between two implementations of the same frozen task.
- A demonstration that a developer can run NCP from Python via `from ncp_runtime import NCP` without configuring an MCP host or writing a graph of their own.
- Bounded by the real limits of the on-repo NCP implementation (3-company catalog, 9-descriptor ICP vocabulary).

### 1.2 What this example IS NOT

- NOT a general LLM-vs-NCP benchmark. 50 fixtures over a 3-company catalog cannot establish statistical superiority of either approach.
- NOT a cost comparison in the full economic sense — this measures **model-API cost only**, not infrastructure cost (CPU, memory, wall-clock process overhead); see §12.3.
- NOT a correctness test of the Haiku-5.5 model in general. The LLM is being measured against **this task**, with **this prompt**, under **this configuration**.
- NOT a vehicle for enlarging the NCP catalog or vocabulary. If the comparison looks less dramatic because of those constraints, that is an honest result (see §1.3).

### 1.3 Honest-constraint declaration

The upstream NCP implementation uses a fixed offline catalog of three fictional companies under IETF-reserved `example.com` subdomains, and a closed 9-descriptor ICP vocabulary. **This example does not change either.** Fixture coverage is therefore bounded: `qualified` / `not_qualified` outcomes are only possible for the three catalog companies; all other inputs fall into `needs_enrichment` or input validation. The README MUST surface this limit before the headline numbers.

## 2. Reference materials (ground truth)

| Artifact | Repo path |
|---|---|
| Graph composition | `examples/graphs/lead-qualification/graph.yaml` |
| Lookup brick manifest | `examples/bricks/company-profile-lookup/manifest.yaml` |
| Lookup brick input schema | `examples/bricks/company-profile-lookup/schemas/input.schema.json` |
| Lookup brick output schema | `examples/bricks/company-profile-lookup/schemas/output.schema.json` |
| Lookup brick source (catalog + URL normalization) | `bricks/company-profile-lookup/src/lib.rs` |
| Qualifier brick manifest | `examples/bricks/icp-qualifier/manifest.yaml` |
| Qualifier brick input schema | `examples/bricks/icp-qualifier/schemas/input.schema.json` |
| Qualifier brick output schema | `examples/bricks/icp-qualifier/schemas/output.schema.json` |
| Qualifier brick source (scoring + outcome) | `bricks/icp-qualifier/src/lib.rs` |
| Vocabulary + matching rules | `examples/bricks/icp-qualifier/SUPPORTED_ICP_VOCABULARY.md` |

If any claim in §§3–6 of this contract diverges from the referenced source, the source wins and this contract is updated.

## 3. Frozen catalog (ground truth for `profile_found`)

Three fictional companies, keyed by normalized hostname:

| Hostname (normalized) | `company_name` | `industry` | `segment` | `audience` |
|---|---|---|---|---|
| `example.com` | Example SaaS | B2B SaaS | SMB | creators |
| `factory.example.com` | Factory Example | manufacturing | enterprise | B2B |
| `shop.example.com` | Shop Example | retail | SMB | B2C |

Any URL that normalizes to one of the three hostnames above yields `profile_found: true`; any other valid hostname yields `profile_found: false`. Hostname normalization: strict offline parse, lowercase ASCII, leading `www.` and single trailing `.` stripped, scheme/path/query/fragment removed. See `bricks/company-profile-lookup/src/lib.rs` for the full rule set, including rejection cases (non-ASCII, userinfo, port, IPv4/IPv6 literal, invalid DNS label).

## 4. Frozen ICP vocabulary (9 descriptors, 3 per dimension)

| Dimension | Descriptors (canonical order) |
|---|---|
| Industry | `b2b saas`, `manufacturing`, `retail` |
| Segment  | `smb`, `mid-market`, `enterprise` |
| Audience | `b2b`, `b2c`, `creators` |

Verbatim from `SUPPORTED_ICP_VOCABULARY.md`.

### 4.1 Token matching rules (both sides must respect these)

- **Case-insensitive** matching: `target_icp` and the descriptor are both lowercased before comparison.
- **Whitespace-tokenized** on ASCII whitespace. Punctuation stays attached to the token, so `"retail,"` is NOT the descriptor `"retail"`.
- **Multi-word descriptors** (`b2b saas`) match when their tokens appear **contiguously** in the token stream.
- **Hyphenated descriptors** (`mid-market`) are single tokens. `"mid market"` (two tokens) does NOT match `mid-market`.
- A token can **contribute to more than one dimension**: `"b2b"` counts for both the `b2b saas` industry descriptor (when followed by `saas`) and the standalone `b2b` audience descriptor.

### 4.2 Dimension interpretability

A dimension is **interpretable** when at least one of its three descriptors appears in `target_icp` under the rules above. If any of the three dimensions is uninterpretable, the business outcome is `needs_enrichment` (see §5.2).

## 5. Business decision rule (frozen)

Both implementations MUST classify each **well-formed** fixture into exactly one of three business outcomes, applying the rule below. Fixtures whose inputs do not conform to the lookup brick's input schema are handled by §6.2 (validation outcome), not by this rule.

### 5.1 Score

For a known catalog profile with all three dimensions interpretable:

```
matched_dimensions = industry_match + segment_match + audience_match   (0..3)
score              = matched_dimensions / 3                            (0, 1/3, 2/3, 1)
```

Where `industry_match`, `segment_match`, `audience_match` are each 1 iff the catalog profile's value for that dimension appears in the recognized descriptor set for that dimension in `target_icp`.

### 5.2 Decision

- `matched_dimensions >= 2` → **`qualified`** (strict majority; `score > 0.5`)
- `matched_dimensions <= 1` → **`not_qualified`** (`score <= 0.5`)
- `profile_found == false` → **`needs_enrichment`** (short-circuit; `target_icp` is not interpreted)
- `profile_found == true` AND any dimension uninterpretable → **`needs_enrichment`** (short-circuit)

**No-fallback rule:** a known catalog profile + fully interpretable `target_icp` + `matched_dimensions == 0` is `not_qualified`, NOT `needs_enrichment`.

## 6. Observable outcomes (frozen vocabulary)

Both implementations emit at most one observable outcome per fixture. The outcome space is:

| Outcome | Kind | Meaning |
|---|---|---|
| `qualified` | business classification | Known profile matches target ICP (strict majority). |
| `not_qualified` | business classification | Known profile does not match target ICP (sub-majority, incl. zero). |
| `needs_enrichment` | business classification | Profile unknown, or target ICP uninterpretable in one or more dimensions. |
| `invalid_input` | **validation outcome** (NOT a business classification) | The input did not conform to the lookup brick's input schema; the implementation observably rejected it as invalid, as opposed to failing for infrastructure reasons. |
| `null` | — | No observable outcome: a runtime failure or infrastructure error that is NOT an input-specific validation rejection. |

### 6.1 Three runtime axes (per implementation)

| Axis | Values | NCP source | LLM source |
|---|---|---|---|
| **Runtime** (did the implementation produce a result?) | `success` / `failure` / `unresolved` | `success`: terminal `result_type == "Success"`. `failure`: terminal `result_type == "Failure"` (any `error_class`). `unresolved`: SDK raised `NCPSDKError` (binary not found, timeout, subprocess error, trace schema error). | `success`: API returned 2xx with a parseable response. `failure`: API `stop_reason == "refusal"` or returned non-parseable text. `unresolved`: HTTP non-2xx (incl. 429, 5xx), `APITimeoutError`, `APIConnectionError`, `max_tokens` cutoff before a classification. |
| **Validation** (did the implementation observably reject invalid input?) | `invalid_input` / `null` | `invalid_input`: terminal `result_type == "Failure"` AND terminal `error.error_class == "INVALID_INPUT"`. All other cases: `null`. | `invalid_input`: `runtime == "success"` AND the extracted label is exactly `invalid_input`. All other cases: `null`. |
| **Business** (did the implementation produce a business classification?) | `qualified` / `not_qualified` / `needs_enrichment` / `null` | `qualified`/`not_qualified`/`needs_enrichment`: the `next_action` from the qualifier terminal when the full pipeline succeeded. `null` otherwise. | The extracted label when `runtime == "success"` AND the label is one of the three business values. `null` otherwise. |

By construction, **at most one of `validation` and `business` is non-null** for any case. A case with `validation == "invalid_input"` cannot also carry a business classification; a case with a business classification is not an input-validation case.

### 6.2 Rejection must be observable AND input-specific

An API outage, a missing NCP binary, a 500 error, a `max_tokens` cutoff — none of these count as a correct `invalid_input` rejection. A validation rejection is a result the implementation **chose to emit after examining the input**, not the absence of any result.

The `classify_result()` function in `scorer.py` is the sole place where `validation` is assigned. It emits `validation="invalid_input"` ONLY for the two cases enumerated in §6.1's Validation row. Any infrastructure failure sets `validation=null`. The scorer in §8.1 relies on this invariant.

## 7. Fixture schema

Fixtures live at `examples/python-sdk/lead-qualification-vs-llm/fixtures/*.json`, one JSON file per logical group. Each fixture object has exactly one of two `expected` shapes.

**Business-classification fixture:**

```json
{
  "id": "lead-0001",
  "input": {
    "company_url": "https://example.com",
    "target_icp": "b2b saas smb creators",
    "notes": null
  },
  "expected": { "business_outcome": "qualified" },
  "category": "catalog_full_match",
  "notes_for_reviewer": "Example SaaS × all three dimensions match."
}
```

**Invalid-input fixture:**

```json
{
  "id": "lead-0046",
  "input": {
    "company_url": "https://exämple.com",
    "target_icp": "b2b saas smb creators",
    "notes": null
  },
  "expected": { "validation": "invalid_input" },
  "category": "invalid_input",
  "notes_for_reviewer": "Non-ASCII hostname — lookup brick rejects with INVALID_INPUT; LLM prompt instructs invalid_input for non-ASCII hostnames."
}
```

Field semantics:

- `id` — stable per-fixture identifier, kebab-case, globally unique.
- `input` — the shape accepted by both implementations' entry functions. Fields correspond 1:1 with the lookup brick's input schema (`company_url`, `target_icp`, optional `notes`).
- `expected` — exactly one of `{"business_outcome": "..."}` or `{"validation": "invalid_input"}`. The `category` field constrains which shape is allowed (invalid_input category ⇔ `expected.validation`; all other categories ⇔ `expected.business_outcome`).
- `category` — bucket for stratified reporting (§8.3). Allowed values: `catalog_full_match`, `catalog_partial_match`, `catalog_no_match`, `catalog_uninterpretable_icp`, `unknown_domain`, `invalid_input`.
- `notes_for_reviewer` — free-text explanation. Not read by code; not passed to the LLM prompt.

### 7.1 Fixture distribution (frozen for `fixtures-rev-01`)

Total: 50 fixtures. **The distribution, inputs, categories, and expected outcomes are frozen before the first measured comparison run.** Exploratory development runs may occur beforehand, but their results do not determine fixture inclusion or weighting. Any subsequent dataset change creates an explicitly identified new revision (e.g., `fixtures-rev-02`), and previously published results remain traceable to their original dataset identity.

| Category | Count | Purpose |
|---|---|---|
| `catalog_full_match` | 6 | 3 companies × 2 fully-matching ICP variants each (word-order + overlap stress) |
| `catalog_partial_match` | 9 | 3 companies × 3 one-dim-mismatch ICP variants |
| `catalog_no_match` | 6 | 3 companies × 2 zero-match-but-fully-interpretable ICPs (the no-fallback rule) |
| `catalog_uninterpretable_icp` | 9 | 3 companies × 3 uninterpretable-dim variants (industry-off, segment-off, audience-off) |
| `unknown_domain` | 15 | Valid non-catalog hostnames with varying ICP interpretability |
| `invalid_input` | 5 | Malformed URLs, non-ASCII hostnames, port, userinfo, scheme errors |

## 8. Correctness evaluation

### 8.1 Per-case scoring (pseudocode)

```python
def score_case(fixture, impl_result):
    runtime, validation, business = classify_result(impl_result)
    expected = fixture["expected"]
    if "validation" in expected:
        # Expected: an observable input-specific validation rejection.
        # Note: this intentionally does NOT require runtime == "success".
        # Per §6.1, NCP's Failure(INVALID_INPUT) is the canonical
        # correct rejection and carries runtime == "failure"; what makes
        # it "correct" is validation == "invalid_input", not the runtime
        # axis. Safety rests on classify_result() setting
        # validation = "invalid_input" ONLY for observable, input-specific
        # rejections (§6.1 Validation row; §6.2). An API outage, missing
        # binary, 5xx, timeout, or any failure unrelated to input
        # validation sets validation = null and therefore cannot
        # accidentally score as a correct rejection.
        correct = (validation == expected["validation"])
    else:
        # Expected: a business classification.
        correct = (runtime == "success") and (business == expected["business_outcome"])
    return {
        "correct": correct,
        "runtime": runtime,
        "validation": validation,
        "business": business,
    }
```

This function is implemented in `scorer.py`. Neither `baseline_llm.py` nor `ncp_version.py` imports `scorer.py`. The implementations return raw results; the scorer evaluates them after the fact.

### 8.2 Headline metrics

Both of the following are reported, side by side:

- **Accuracy across all attempted cases.** Denominator = 50. A runtime failure, unresolved result, or wrong-axis answer counts as incorrect, EXCEPT when the fixture expected a validation rejection and the implementation produced the matching observable `validation` outcome (see §8.1). An NCP `Failure(INVALID_INPUT)` on an invalid-input fixture is correct; the same `Failure` on a business-classification fixture is incorrect.
- **Accuracy among completed cases.** Denominator = count of cases where the implementation produced an **observable outcome** — either a successful business classification (`runtime == "success"` and `business` non-null) or an explicit input-validation rejection (`validation == "invalid_input"`, regardless of runtime axis). Infrastructure failures (`unresolved`, or `failure` with `validation == null`) are excluded from the denominator and the numerator.

Alongside both: **completion rate** = (count of cases with an observable outcome) / 50. This metric treats NCP's `Failure(INVALID_INPUT)` and the LLM's `invalid_input` label as equally "completed" — both are decisions the implementation reached by examining the input, and the metric is comparable across implementations despite their different error-reporting conventions.

Neither metric is suppressed. The README presents both side by side.

### 8.3 Per-category results

A table reporting all three numbers per `category`, so no bucket can quietly skew a global average.

### 8.4 Latency

Per-case wall-clock from the moment the implementation function is called to the moment it returns. Reported as:

- **p50** and **p95** across all 50 cases.
- **Platform** (OS + CPU model + Python version + which runner, if CI).
- Single run per case is the default — enough for an illustrative comparison, not a rigorous latency benchmark; the README states this explicitly.

Both implementations run on the same host in the same `comparison.json` run.

### 8.5 Model-API cost

For the LLM side only. Computed per case as:

```
cost = (usage.input_tokens  / 1e6) * pricing.input_price_per_mtok
     + (usage.output_tokens / 1e6) * pricing.output_price_per_mtok
```

Values come from the Anthropic API response's `usage` object — **provider-reported only, never tokenizer estimates**. If the provider does not return a usage object for an attempted request (e.g., early HTTP error), the per-case `tokens_in`/`tokens_out`/`model_cost_usd` are recorded as `null`, never silently 0. Pricing comes from `pricing.json`.

For the NCP side, `tokens_in`/`tokens_out`/`model_cost_usd` are exactly `0` (deterministic absence of LLM involvement), not `null`.

## 9. Measurement protocol (frozen)

### 9.1 LLM baseline

- **Model ID:** `claude-haiku-5-5` (undated alias; the current-generation Haiku as of this contract's publication).
- **Thinking:** disabled explicitly via `thinking={"type": "disabled"}`. Required because Haiku 5.5 defaults to adaptive thinking on; we are measuring the model's non-reasoning behavior for a fair cost comparison.
- **Effort:** `output_config={"effort": "low"}`. Required because `thinking: {"type": "disabled"}` is only accepted at effort `high` or lower; `low` is the minimum.
- **max_tokens:** 256. Enough for a label + brief reasoning; small enough to catch runaway outputs via a `max_tokens` cutoff.
- **Caching:** disabled. No `cache_control` on any request.
- **Retries:** `Anthropic(max_retries=0)` — no SDK-level retries. One attempted request per invocation; a failed request is recorded as such, not retried into a different outcome.
- **No fallbacks**, no streaming, no batching.
- **Request transport:** `anthropic` Python SDK, synchronous `client.messages.create(...)`.
- **Allowed output labels:** `qualified`, `not_qualified`, `needs_enrichment`, `invalid_input` (four labels; `invalid_input` is a validation outcome, not a business classification — see §6).
- **Prompt template:** single frozen template, filled with the fixture's `company_url`, `target_icp`, and (optionally) `notes`. The template contains:
  - The 3-company catalog (verbatim from §3)
  - The 9-descriptor ICP vocabulary (verbatim from §4)
  - The business decision rule (verbatim from §5)
  - The validation rejection rule (invalid-input conditions matching the lookup brick's rejection cases: non-ASCII hostname, userinfo, port, unsupported scheme, invalid DNS label, empty/too-long fields)
  - The four allowed output labels, with instruction to output exactly one on a single line, followed by a brief reason.
- The prompt NEVER contains the fixture's `expected`, `id`, `category`, or `notes_for_reviewer`. Enforcement is by construction and behavioral invariant (§10), not by substring search.

### 9.2 NCP side

- **SDK:** `from ncp_runtime import NCP` from the installed `ncp-runtime` package.
- **Binary resolution:** the SDK's own precedence is `binary=` kwarg → bundled wheel binary → `shutil.which("ncp")`. This example's `ncp_version.py` reads `NCP_RUNTIME` from the environment (if set) and passes it as the explicit `binary=` kwarg: `NCP(binary=os.environ.get("NCP_RUNTIME"))`. If `NCP_RUNTIME` is unset, `NCP()` is constructed with no `binary=` and the SDK's own precedence applies. `NCP_RUNTIME` is a convention of this example (and the CI harness), not an SDK discovery step.
- **Graph:** `examples/graphs/lead-qualification/graph.yaml` (unchanged).
- **Brick dir:** `examples/bricks/` (parent of the two brick directories; unchanged).
- **No changes** to the graph, the catalog, the vocabulary, or the bricks. If the comparison would benefit from such changes, that observation goes in a follow-up PR against the brick, not into this example.

### 9.3 Pricing anchor

`pricing.json` carries, at minimum:

```json
{
  "model": "claude-haiku-5-5",
  "input_price_per_mtok": 0.10,
  "output_price_per_mtok": 0.50,
  "pricing_date": "2026-10-30",
  "pricing_source_url": "https://docs.claude.com/en/docs/about-claude/models/overview"
}
```

Pricing is tied to a specific date; a change in Anthropic's prices after that date does not retroactively invalidate a prior run — it simply means the next run bumps `pricing_date` and re-records.

### 9.4 Comparison output

`comparison.json` is the machine-readable output of `compare.py`. Shape:

```json
{
  "schema_version": "lead-qual-comparison-0.1",
  "run": {
    "timestamp_utc": "...",
    "platform": { "os": "...", "python": "...", "ci": false },
    "ncp_binary_version": "0.3.6",
    "ncp_runtime_sdk_version": "0.1.0",
    "llm_model": "claude-haiku-5-5",
    "llm_model_resolved": "claude-haiku-5-5-20261001",
    "pricing_date": "2026-10-30",
    "fixture_set_revision": "fixtures-rev-01",
    "fixture_set_sha256": "...",
    "prompt_template_sha256": "..."
  },
  "per_case": [
    {
      "id": "lead-0001",
      "category": "catalog_full_match",
      "expected": { "business_outcome": "qualified" },
      "ncp": {
        "runtime": "success",
        "validation": null,
        "business": "qualified",
        "latency_ms": 123.4,
        "model_api_calls": 0,
        "graph_invocations": 1,
        "tokens_in": 0,
        "tokens_out": 0,
        "model_cost_usd": 0.0
      },
      "llm": {
        "runtime": "success",
        "validation": null,
        "business": "qualified",
        "latency_ms": 456.7,
        "model_api_calls": 1,
        "graph_invocations": 0,
        "tokens_in": 312,
        "tokens_out": 18,
        "model_cost_usd": 0.0000402
      }
    }
  ],
  "aggregates": {
    "accuracy_all_attempted":  { "ncp": 0.00, "llm": 0.00 },
    "accuracy_completed_only": { "ncp": 0.00, "llm": 0.00 },
    "completion_rate":         { "ncp": 0.00, "llm": 0.00 },
    "latency_ms_p50":          { "ncp": 0.0,  "llm": 0.0  },
    "latency_ms_p95":          { "ncp": 0.0,  "llm": 0.0  },
    "total_model_api_calls":   { "ncp": 0,    "llm": 0    },
    "total_graph_invocations": { "ncp": 0,    "llm": 0    },
    "total_tokens_in":         { "ncp": 0,    "llm": null },
    "total_tokens_out":        { "ncp": 0,    "llm": null },
    "total_model_cost_usd":    { "ncp": 0.00, "llm": null },
    "measured_subtotals": {
      "llm": {
        "cases_with_usage":     0,
        "cases_without_usage":  0,
        "partial_tokens_in":    0,
        "partial_tokens_out":   0,
        "partial_model_cost_usd": 0.00
      }
    }
  },
  "per_category": {
    "catalog_full_match":          { "ncp": {}, "llm": {} },
    "catalog_partial_match":       { "ncp": {}, "llm": {} },
    "catalog_no_match":            { "ncp": {}, "llm": {} },
    "catalog_uninterpretable_icp": { "ncp": {}, "llm": {} },
    "unknown_domain":              { "ncp": {}, "llm": {} },
    "invalid_input":               { "ncp": {}, "llm": {} }
  }
}
```

**Null-propagation rule for aggregates.** If any attempted LLM request lacks provider-reported usage (`per_case[i].llm.tokens_in` is `null`, etc.), the LLM-side aggregate `total_tokens_in` / `total_tokens_out` / `total_model_cost_usd` are set to `null` rather than a sum that silently excludes the unknown values. The known-measured subtotals are reported separately under `aggregates.measured_subtotals.llm` with explicit `cases_with_usage` / `cases_without_usage` counts, so the reader can distinguish "cost is unknown" from "cost is low." A failed API request can never make total measured cost appear artificially low.

**`graph_invocations` semantic.** This field counts calls submitted to `NCP.run()` by the adapter, not confirmed Rust subprocess starts. An `NCP.run()` call that fails during SDK pre-flight (binary resolution, runtime-version probe) counts as 1 — the SDK call was attempted. A value of 0 means the adapter reached its return path without reaching `NCP.run()`; this is a defensive branch that in practice is unreachable because a programmer-level failure in `NCP()` construction propagates as an exception rather than being normalized into an `AdapterResult`. This definition is observable from the adapter's perspective without additional subprocess instrumentation. The LLM-side analog is `model_api_calls`: counts attempted API requests under §9.1's `max_retries=0` rule, not successful 2xx responses.

**`run.llm_model_resolved`.** The provider's actual returned model identifier (from `response.model` on the first LLM response in the batch that reported one), preserved alongside the requested `llm_model` alias so published results stay interpretable if the alias ever resolves to a different snapshot. `null` when no LLM request succeeded or every `response.model` was absent.

The NCP side's token/cost totals are always numeric (always 0), because the deterministic absence of LLM involvement is known with certainty.

The README table renders FROM a committed `comparison.json` (§11); no number in the README is hand-edited.

## 10. Fairness invariants (verified by `fairness-linter`)

A test in `tests/test_fairness_linter.py` asserts:

1. Both implementations are invoked with the **same** list of fixtures in the **same** order (indexed by `id`), and both receive the **same** `input` object per fixture (field equality after JSON round-trip).
2. **Prompt-input containment** (behavioral invariant). The comparison orchestrator passes only `fixture["input"]` to the LLM-side entry function, and the prompt-rendering function accepts a single argument of that input shape. A test asserts:
   - The renderer's signature exposes exactly one parameter and no default-value or module-level channel for fixture metadata.
   - A **metadata-mutation test**: for each fixture, render the prompt with `input` held constant while `expected`, `category`, and `notes_for_reviewer` are mutated to arbitrary other legal values; the rendered prompt string must be byte-identical across all mutations. If the renderer silently reads any fixture metadata, this test catches it as a behavioral regression. (This replaces an unverifiable "no code path reads X" claim with a checkable invariant.)
3. The prompt template string's **SHA-256** is recorded in `comparison.json` under `run.prompt_template_sha256`, and matches the hash committed in `prompt-template.sha256` next to `baseline_llm.py`. A template change without a committed hash bump fails the linter.
4. The allowed-output-label set is the **same four values** for both sides: `qualified`, `not_qualified`, `needs_enrichment`, `invalid_input`.
5. The fixture-set SHA-256 (`run.fixture_set_sha256`) matches the committed `fixtures-rev-01.sha256`.

## 11. CI policy and published-results archive

- **CI job (`python-sdk-example-test`, in `.github/workflows/rust.yml`):** runs the NCP side only against the fixtures and asserts the fairness linter passes. No API keys in CI. The LLM side is run locally by the maintainer when producing published results.
- **Exploratory `comparison.json` runs are NOT committed.** They are written to `local/comparison-*.json` under the example directory, gitignored.
- **The one `comparison.json` whose numbers back the published README IS committed**, under:
  `examples/python-sdk/lead-qualification-vs-llm/published/comparison.json`
  Alongside it, in the same `published/` directory:
  - `fixture-set.json` — the frozen fixture-set snapshot (equal to the on-repo fixtures at run time; its SHA-256 matches `run.fixture_set_sha256`)
  - `prompt-template.txt` — the exact prompt template string (its SHA-256 matches `run.prompt_template_sha256`)
  - `pricing.json` — a snapshot of the pricing anchor used
  - `environment.json` — OS, Python version, CPU model, SDK versions, `ncp` binary version
  - `requests/*.jsonl` — the LLM request/response records for every attempted call, **sanitized**: no `Authorization` header, no `x-api-key`, no org-ID header, no account-identifying request-ID metadata beyond the per-request `id` field. Full response bodies are retained minus those sensitive fields.
- The README's table renders FROM `published/comparison.json`. Updating published results means replacing all of `published/` in a single commit so artifacts stay internally consistent.

## 12. What is intentionally NOT measured

### 12.1 Catalog coverage breadth

Three companies is the ceiling. See §1.3.

### 12.2 LLM reasoning strength

Thinking is disabled on the baseline (§9.1). This is a measurement of Haiku 5.5's non-reasoning behavior at effort `low`. The example does NOT claim NCP outperforms a reasoning LLM; it demonstrates the cost of substituting an LLM for a deterministic classifier where the deterministic path exists.

### 12.3 Infrastructure cost

Model API cost (§8.5) is NOT total execution cost. NCP consumes CPU/memory/wall-clock on the host; the LLM baseline consumes a tiny amount of local wall-clock plus the API round-trip. Neither is monetized here. The README states this explicitly: "model-API cost ≠ total cost".

### 12.4 Statistical significance

50 fixtures over a 3-company catalog is not a statistically significant sample. The README calls this a **measured example**, not a benchmark. No p-values, no confidence intervals.

### 12.5 Prompt-engineering optimization

The LLM prompt is deliberate but not iteratively tuned against the fixture set. Tuning the prompt against the labels would convert this into a benchmark optimized to make NCP look worse. If the LLM baseline is weak because the prompt is weak, that is an honest result and the README invites readers to try their own prompt against the same fixtures.

## 13. Live-API verification (required before merge)

Before this contract becomes final (i.e., before PR 3 is merged), the exact request configuration in §9.1 MUST be verified against the live Anthropic API. The verification is a single-case request against `claude-haiku-5-5` with `thinking: {"type": "disabled"}` + `output_config: {"effort": "low"}` + `max_tokens: 256` + `max_retries=0`, run against one fixture.

The verification records in `verification/api-check.log` (ignored by git):

- The exact request body sent.
- The HTTP status + response `id`.
- The response `stop_reason`, `usage` object, and extracted label.
- Any 400 reason or unexpected field.

If verification succeeds, this contract is updated to list the request ID + ISO timestamp as the "Last live-API verification" header.

If verification fails, this contract is amended with the actual accepted configuration before PR 3 merges — the untested configuration never becomes contractual.

## 14. Change control

**Before PR 3 merges:** changes to this contract require explicit review (noted in the PR thread, with the diff visible on a per-section basis) and rerunning any test suites, linters, or verification steps affected by the change (fairness linter, live-API verification, scorer). No separate PR is required.

**After PR 3 merges:** substantive changes to this contract use normal follow-up PRs against `main`. A fixture-set change triggers a new `fixtures-rev-NN` identity (§7.1); a comparison-schema change bumps `comparison.json`'s `schema_version`; the published-results archive in `published/` is replaced wholesale in a single commit to keep its artifacts internally consistent (§11).

End of contract.
