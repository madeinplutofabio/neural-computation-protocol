<!--
SPDX-License-Identifier: Apache-2.0
Copyright 2026 Fabio Marcello Salvadori
-->

# Lead qualification: NCP vs LLM — a reproducible comparison

A reproducible side-by-side comparison between a deterministic NCP
graph and Anthropic's Claude Haiku 5.5 on the same lead-qualification
task. Both sides see identical inputs; correctness is scored against
a frozen ground-truth label per fixture.

**This is an adoption example, not a benchmark.** Read the
honest-limits section before interpreting any numbers.

## Honest limits — what this example is NOT

Read before interpreting the numbers.

1. **Not a general LLM-vs-NCP benchmark.** 50 fixtures over a
   3-company catalog cannot establish statistical superiority of
   either approach. The upstream NCP implementation uses a fixed
   offline catalog of three fictional companies under IETF-reserved
   `example.com` subdomains. `qualified` / `not_qualified` outcomes
   are only possible for those three companies; everything else
   routes to `needs_enrichment` or input-validation rejection.
2. **Not a total-cost comparison.** Only **model-API cost** is
   measured. NCP consumes CPU/memory/wall-clock on the host; the
   LLM consumes API round-trips. Neither is monetized as
   infrastructure cost here.
3. **Not a reasoning-capability test.** The LLM baseline runs with
   `thinking: {"type": "disabled"}` at `effort: "low"`. This measures
   Haiku 5.5's non-reasoning behavior for a fair cost comparison —
   not what the model is capable of under its usual configuration.
4. **Not a statistical result.** 50 cases is not statistically
   significant. The README reports per-category and all-attempted
   accuracy explicitly so a reader can judge each bucket.
5. **Not a vehicle for scope expansion.** If the comparison would
   look more favorable with a larger catalog or a different prompt,
   that's an honest limit — not a reason to change the NCP graph or
   the frozen dataset. Those changes would require a new fixture
   revision (`fixtures-rev-NN`) and a new comparison.

The full set of methodological commitments is in
[CONTRACT.md](./CONTRACT.md).

## Headline results

Measured on 2026-10-31 against `fixtures-rev-01`, `claude-haiku-5-5`
with thinking disabled at effort `low`, pricing anchor `2026-10-31`.
Full per-case breakdown + sanitized metadata lives in
[`published/comparison.json`](./published/comparison.json). This
section was populated from that file by hand; the exact table format
is enforced by
[`tests/test_published_consistency.py`](./tests/test_published_consistency.py),
and every cell matches the file's aggregates to within the display
precision tolerances documented in that test.

**NCP's `total_model_cost_usd = $0.00000000` means zero model-API cost,
not zero infrastructure cost.** The NCP side still consumes CPU, memory
and wall-clock on the host; those costs are not monetized here. See
[Honest limits](#honest-limits--what-this-example-is-not) point 2.

<!-- RESULTS_TABLE_START -->

| Metric | NCP | LLM |
|---|---|---|
| accuracy_all_attempted | 100.00% | 60.00% |
| accuracy_completed_only | 100.00% | 60.00% |
| completion_rate | 100.00% | 100.00% |
| latency_ms_p50 | 48.4 | 734.2 |
| latency_ms_p95 | 50.9 | 1207.5 |
| total_model_api_calls | 0 | 50 |
| total_graph_invocations | 50 | 0 |
| total_tokens_in | 0 | 159094 |
| total_tokens_out | 0 | 364 |
| total_model_cost_usd | $0.00000000 | $0.01609140 |

<!-- RESULTS_TABLE_END -->

### Where the 20 LLM mismatches fell

Observable per-category pattern (no causal claim about why any
individual decision was made):

| Category | Fixtures | NCP | LLM | LLM pattern on mismatches |
|---|---|---|---|---|
| `catalog_full_match` | 6 | 6/6 | 5/6 | 1 flipped to `not_qualified` |
| `catalog_no_match` | 6 | 6/6 | 6/6 | — |
| `catalog_partial_match` | 9 | 9/9 | 1/9 | 8 flipped to `not_qualified` |
| `catalog_uninterpretable_icp` | 9 | 9/9 | 0/9 | 9 returned `not_qualified` instead of `needs_enrichment` |
| `unknown_domain` | 15 | 15/15 | 13/15 | 2 flipped to `invalid_input` |
| `invalid_input` | 5 | 5/5 | 5/5 | — |

The largest observed differences were in `catalog_partial_match`
(1/9 correct) and `catalog_uninterpretable_icp` (0/9 correct). These
are results from this frozen synthetic workload, not evidence that
the same pattern generalizes to other tasks or model configurations.

See `published/comparison.json` for the per-case breakdown, run
metadata, and sanitized request/response records.

## Identifiers (traceability)

The published numbers must trace back to these artifacts. Any
divergence is a CI failure in
[`tests/test_published_consistency.py`](./tests/test_published_consistency.py).

| Artifact | Identity |
|---|---|
| **Dataset revision** | `fixtures-rev-01` |
| **Dataset SHA-256** | `12e4d5c61e5d3174365021a03fc47ed6dfe63422b50224e9e30b1c56d4dbb170` |
| **Prompt template SHA-256** | `05ddefdbfc9bc00a71926229573ca872461d4c8601589b6553fcfb4fea9c4d1d` |
| **LLM model** | `claude-haiku-5-5` (short-context tier) |
| **Pricing anchor** | `2026-10-31`, $0.10 / $0.50 per MTok input/output |
| **Comparison schema** | `lead-qual-comparison-0.1` |

**Environment note.** `run.ncp_binary_version` in
`published/comparison.json` is `null`: the orchestrator does not
itself probe the binary's `--version` output. The runtime was
independently verified at `ncp 0.3.6` at measurement time, matching
`supported_runtime_versions`. Future SDK revisions may surface this
field automatically; the current frozen orchestrator does not.

## The two implementations

Both are called by [`compare.py`](./compare.py) with byte-identical
deep-copied inputs per fixture. Correctness scoring in
[`scorer.py`](./scorer.py) is provider-independent.

- **[`ncp_version.py`](./ncp_version.py)** — invokes the
  deterministic graph via `from ncp_runtime import NCP`, calling
  `NCP().run(graph=..., brick_dir=..., inputs=...)` once per fixture.
  Zero LLM calls, zero tokens, zero API cost.
- **[`baseline_llm.py`](./baseline_llm.py)** — one Anthropic API
  call per fixture: `claude-haiku-5-5`, thinking disabled, effort
  low, `max_tokens=256`, `max_retries=0`. The frozen prompt template
  is [`prompt-template.txt`](./prompt-template.txt) (SHA-256
  recorded in [`prompt-template.sha256`](./prompt-template.sha256)).

The orchestrator passes only `fixture["input"]` (three fields:
`company_url`, `target_icp`, `notes`) to both. Fixture metadata
(`id`, `expected`, `category`, `notes_for_reviewer`) is never
reachable from either adapter — see
[CONTRACT.md](./CONTRACT.md) §10 fairness invariants and
[`tests/test_fairness_linter.py`](./tests/test_fairness_linter.py).

## Reproduce

**Every command block below assumes a fresh shell at the repository
root.** Each block is self-contained; the `cd` to the example
directory is included inside the block where needed.

### Install

Install the SDK and example dependencies:

```bash
pip install -e python/ncp-runtime/
pip install -r examples/python-sdk/lead-qualification-vs-llm/requirements.txt
```

For the NCP side, build the runtime once:

```bash
cargo build --release -p ncp-runtime
export NCP_RUNTIME="$PWD/target/release/ncp"
```

On Windows PowerShell, set the environment variable with:

```powershell
$env:NCP_RUNTIME = "$(Get-Location)\target\release\ncp.exe"
```

### NCP-only run (no API key required)

```bash
cd examples/python-sdk/lead-qualification-vs-llm
python compare.py
```

Writes `local/comparison-<microsecond-timestamp>.json`. The `local/`
directory is gitignored; files here are for exploration, not
publication.

### LLM run (requires ANTHROPIC_API_KEY)

**Prerequisite.** Set `ANTHROPIC_API_KEY` securely in your
environment before running the LLM side (e.g., using your shell's
startup file, a secrets manager, or an `.env` loader). Do not paste
the key into these commands or commit it to the repository.

```bash
cd examples/python-sdk/lead-qualification-vs-llm
python compare.py --run-llm
```

Writes `local/comparison-<microsecond-timestamp>.json` with both
sides populated. The `local/` directory is gitignored.

### Produce the published artifact

Prerequisite is the same as above: `ANTHROPIC_API_KEY` must be set
securely in your environment before running.

```bash
cd examples/python-sdk/lead-qualification-vs-llm
python compare.py --run-llm --publish
```

Writes `published/comparison.json` (tracked by git). Enforces full
dataset + identity + case + ground-truth + aggregate-consistency
validation before writing. If any invariant fails, nothing is
written and the maintainer sees an explicit error.

### Publishing measured results

Running `--publish` writes `published/comparison.json`. It DOES NOT
update this README. Publication is a three-step process:

1. Run `python compare.py --run-llm --publish` (from the example
   directory, as shown above) to generate
   `published/comparison.json`.
2. **Manually populate** the `RESULTS_TABLE_START` /
   `RESULTS_TABLE_END` block in this README with a markdown table
   matching the 10 headline aggregates. Use the formatting rules
   documented inside the comment block in
   [Headline results](#headline-results) (2-decimal percent,
   1-decimal millisecond, 8-decimal USD, integer counts; literal
   `null` for null aggregates).
3. Rerun the test suite. Once both `published/comparison.json` and
   the populated README table exist,
   [`tests/test_published_consistency.py`](./tests/test_published_consistency.py)
   runs the full consistency check and fails if any table cell
   diverges from the published aggregate beyond the
   display-precision tolerance.

Automatic README generation is deliberately out of scope: the
manual step is the forcing function that makes the maintainer read
the numbers before committing them.

### Run the test suite

```bash
cd examples/python-sdk/lead-qualification-vs-llm
python -m pytest -q tests/
```

Current expected result (pre-measurement, no `published/`): **169
passed, 11 skipped**. The 11 skips are the published-consistency
tests that cannot run until `published/comparison.json` exists.

## Files

```
├── CONTRACT.md                    # frozen methodology
├── compare.py                     # orchestrator
├── scorer.py                      # provider-independent scoring
├── ncp_version.py                 # NCP-side adapter
├── baseline_llm.py                # LLM-side adapter
├── fixtures/                      # 50 frozen cases (6 files)
├── fixtures-rev-01.sha256         # dataset identity
├── prompt-template.txt            # frozen LLM prompt
├── prompt-template.sha256         # prompt identity
├── pricing.json                   # pricing anchor
├── requirements.txt               # anthropic + pytest
├── tests/                         # 6 test files
├── published/comparison.json      # measured results (created by --publish)
├── local/                         # per-run exploratory outputs (gitignored)
└── verification/                  # pre-merge check logs (gitignored)
```

## Reference

- [CONTRACT.md](./CONTRACT.md) — complete evaluation methodology
- [fixtures/](./fixtures/) — 50 frozen cases, 6 files by category
- [prompt-template.txt](./prompt-template.txt) — exact LLM prompt
- [tests/test_published_consistency.py](./tests/test_published_consistency.py) — enforces this README against published aggregates
