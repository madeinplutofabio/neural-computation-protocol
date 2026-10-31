# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Published-results consistency validator.

Three groups of tests:

  * ALWAYS run: parser-helper semantics (null placeholders, raise on
    malformed, numeric formats, non-finite rejection).
  * Run when `published/comparison.json` exists: identity, pricing,
    model, schema, resolved-model-agreement checks + a strict pass
    through _validate_publication_ready.
  * Run when both `published/` and `README.md` exist: reader
    traceability + machine-parsed RESULTS_TABLE match against
    comparison.json aggregates.

When the required artifacts are absent, the corresponding tests skip
cleanly rather than fail. Once an artifact exists, inconsistent data
fails loudly — never silently.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import compare  # noqa: E402


PUBLISHED_PATH = compare.EXAMPLE_DIR / "published" / "comparison.json"
README_PATH = compare.EXAMPLE_DIR / "README.md"

_PUBLISHED_EXISTS = PUBLISHED_PATH.exists()
_README_EXISTS = README_PATH.exists()

_skip_no_published = pytest.mark.skipif(
    not _PUBLISHED_EXISTS,
    reason="published/comparison.json does not exist yet",
)
_skip_no_readme = pytest.mark.skipif(
    not _README_EXISTS,
    reason="README.md does not exist yet",
)


# --- Helpers (shared by parser tests and README test) --------------------


_NULL_PLACEHOLDERS: frozenset[str] = frozenset({"null", "n/a", "—", "-"})

_FP_EPSILON = 1e-12  # floating-point safety margin, well below display precision


def _parse_metric_cell(raw: str) -> float | int | None:
    """Parse a RESULTS_TABLE cell value.

    Returns None iff the stripped value matches an explicit null
    placeholder. Returns int for plain whole-number counts and float
    for percentages, currency, and other numeric values.

    Raises ValueError if the value is neither a null placeholder nor
    a parseable finite number. "Malformed cell" is semantically
    distinct from "unknown value"; conflating them would allow a
    display bug to silently match a null aggregate. Non-finite values
    (nan, inf, -inf) are also rejected.
    """
    stripped = raw.strip()
    if stripped.lower() in _NULL_PLACEHOLDERS:
        return None
    try:
        if stripped.endswith("%"):
            v = float(stripped[:-1]) / 100.0
        elif stripped.startswith("$"):
            v = float(stripped[1:])
        else:
            v = float(stripped)
    except ValueError as exc:
        raise ValueError(
            f"README results cell {raw!r} is neither a null placeholder "
            f"nor a parseable number"
        ) from exc
    if not math.isfinite(v):
        raise ValueError(
            f"README results cell {raw!r} parses to a non-finite value "
            f"({v!r}); measurements must be finite"
        )
    is_integer_source = (
        not stripped.endswith("%")
        and not stripped.startswith("$")
        and "." not in stripped
    )
    if is_integer_source and v.is_integer():
        return int(v)
    return v


def _tolerance_for(metric: str) -> float:
    """Maximum absolute difference between a README display cell and
    the comparison aggregate for a given metric.

    README formatting contract (File 12 must honor):
      * Accuracy / completion: 2-decimal percentage, '83.33%'
          → half-unit = 0.00005
      * Latency: 1-decimal ms, '123.4'
          → half-unit = 0.05 ms
      * Cost: 8-decimal USD, '$0.00001234'
          → half-unit = 5e-9
      * Counts / tokens / calls: integer
          → tolerance = 0 (exact)
      * Unknown: 'null' (exact null comparison)
    """
    if metric.startswith("total_") and "cost" not in metric:
        return 0.0
    if "cost" in metric:
        return 5e-9 + _FP_EPSILON
    if "accuracy" in metric or metric == "completion_rate":
        return 5e-5 + _FP_EPSILON
    if "latency" in metric:
        return 0.05 + _FP_EPSILON
    return 0.0


_HEADLINE_METRICS: tuple[str, ...] = (
    "accuracy_all_attempted",
    "accuracy_completed_only",
    "completion_rate",
    "latency_ms_p50",
    "latency_ms_p95",
    "total_model_api_calls",
    "total_graph_invocations",
    "total_tokens_in",
    "total_tokens_out",
    "total_model_cost_usd",
)

_RESULTS_START = "<!-- RESULTS_TABLE_START -->"
_RESULTS_END = "<!-- RESULTS_TABLE_END -->"


def _extract_readme_results_table(readme_text: str) -> dict[str, dict[str, str]]:
    """Parse the sentinel-delimited headline results table.

    Returns {metric_name: {"ncp": raw_cell, "llm": raw_cell}} with
    the raw string cell values. The README must emit a markdown table
    with exactly 3 columns: Metric | NCP | LLM.
    """
    start = readme_text.find(_RESULTS_START)
    end = readme_text.find(_RESULTS_END)
    if start == -1 or end == -1 or end <= start:
        raise ValueError(
            "README missing RESULTS_TABLE_START/END sentinels; cannot "
            "verify measured numbers against published/comparison.json"
        )
    block = readme_text[start + len(_RESULTS_START) : end]
    out: dict[str, dict[str, str]] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if set(stripped.replace("|", "").strip()) <= {"-", ":", " "}:
            continue  # markdown separator row
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) != 3:
            continue
        metric, ncp_val, llm_val = cells
        if metric.lower() in ("metric", ""):
            continue  # header row
        out[metric] = {"ncp": ncp_val, "llm": llm_val}
    return out


def _collect_per_case_resolved_models(doc: dict) -> list[str]:
    """Return non-null llm_model_resolved values from per_case.llm."""
    out: list[str] = []
    for entry in doc.get("per_case", []):
        side = entry.get("llm")
        if side is None:
            continue
        resolved = side.get("llm_model_resolved")
        if resolved is not None:
            out.append(resolved)
    return out


def _load_published() -> dict:
    return json.loads(PUBLISHED_PATH.read_text(encoding="utf-8"))


# --- Parser semantics (always run) ---------------------------------------


def test_parse_metric_cell_handles_null_placeholders():
    """All documented null-like placeholders parse to None."""
    for null_like in ("null", "NULL", "n/a", "N/A", "—", "-"):
        assert _parse_metric_cell(null_like) is None
    assert _parse_metric_cell("  null  ") is None  # whitespace stripped


def test_parse_metric_cell_raises_on_malformed_value():
    """Malformed cells raise ValueError — NOT silently None. A
    formatting bug (e.g. 'approximately 42') must not quietly match
    a null aggregate. Non-finite values are also malformed."""
    for bad in (
        "abc",
        "n/a maybe",
        "forty-two",
        "~=42",
        "%42",
        "$",
        "nan",
        "inf",
        "-inf",
        "$nan",
        "nan%",
    ):
        with pytest.raises(ValueError):
            _parse_metric_cell(bad)


def test_parse_metric_cell_parses_valid_formats():
    """Spot-check each supported numeric format."""
    assert _parse_metric_cell("42") == 42
    assert isinstance(_parse_metric_cell("42"), int)
    assert _parse_metric_cell("42.0") == 42.0
    assert isinstance(_parse_metric_cell("42.0"), float)
    assert _parse_metric_cell("3.14") == pytest.approx(3.14)
    assert _parse_metric_cell("83.33%") == pytest.approx(0.8333)
    assert _parse_metric_cell("$0.00001234") == pytest.approx(0.00001234)
    assert _parse_metric_cell("0") == 0
    assert _parse_metric_cell("$0.00000000") == pytest.approx(0.0)


# --- Publication identity (require published/) --------------------------


@_skip_no_published
def test_published_passes_strict_publication_validator():
    """published/comparison.json must pass _validate_publication_ready
    against the current committed fixtures + template."""
    doc = _load_published()
    compare._validate_publication_ready(doc)


@_skip_no_published
def test_published_fixture_hash_matches_committed_file():
    doc = _load_published()
    committed = compare.FIXTURE_SET_HASH_PATH.read_text(encoding="utf-8").strip()
    assert doc["run"]["fixture_set_sha256"] == committed


@_skip_no_published
def test_published_prompt_hash_matches_committed_file():
    doc = _load_published()
    committed = compare.PROMPT_TEMPLATE_HASH_PATH.read_text(encoding="utf-8").strip()
    assert doc["run"]["prompt_template_sha256"] == committed


@_skip_no_published
def test_published_fixture_set_revision_matches_constant():
    doc = _load_published()
    assert doc["run"]["fixture_set_revision"] == compare.FIXTURE_SET_REVISION


@_skip_no_published
def test_published_pricing_date_matches_pricing_json():
    doc = _load_published()
    pricing_data = json.loads(compare.PRICING_PATH.read_text(encoding="utf-8"))
    assert doc["run"]["pricing_date"] == pricing_data["pricing_date"]


@_skip_no_published
def test_published_llm_model_matches_compare_constant():
    doc = _load_published()
    assert doc["run"]["llm_model"] == compare.LLM_MODEL


@_skip_no_published
def test_published_run_level_resolved_model_equals_first_per_case_value():
    """run.llm_model_resolved must equal the first non-null
    llm_model_resolved recorded in the LLM per-case results."""
    doc = _load_published()
    per_case_resolved = _collect_per_case_resolved_models(doc)
    run_level = doc["run"].get("llm_model_resolved")
    if not per_case_resolved:
        assert run_level is None, (
            f"run.llm_model_resolved is {run_level!r} but no per_case "
            f"recorded a non-null llm_model_resolved value"
        )
        return
    assert run_level == per_case_resolved[0], (
        f"run.llm_model_resolved={run_level!r} does not equal the "
        f"first non-null per_case value={per_case_resolved[0]!r}"
    )


@_skip_no_published
def test_published_per_case_resolved_models_all_agree():
    """All non-null per-case llm_model_resolved values must be identical."""
    doc = _load_published()
    per_case_resolved = _collect_per_case_resolved_models(doc)
    if not per_case_resolved:
        return
    distinct = set(per_case_resolved)
    assert len(distinct) == 1, (
        f"per-case llm_model_resolved values disagree: "
        f"{sorted(distinct)!r}. The batch was served by multiple models "
        f"within a single run."
    )


@_skip_no_published
def test_published_schema_version_matches_current():
    doc = _load_published()
    assert doc["schema_version"] == compare.COMPARISON_SCHEMA_VERSION


# --- README traceability + measured-numbers match (require both) --------


@_skip_no_published
@_skip_no_readme
def test_readme_references_fixture_set_revision_and_pricing_date():
    """A reader must be able to tell which dataset + pricing anchor
    produced the README's measured numbers."""
    doc = _load_published()
    readme_text = README_PATH.read_text(encoding="utf-8")
    assert doc["run"]["fixture_set_revision"] in readme_text, (
        f"README does not reference fixture_set_revision="
        f"{doc['run']['fixture_set_revision']!r}"
    )
    assert doc["run"]["pricing_date"] in readme_text, (
        f"README does not reference pricing_date={doc['run']['pricing_date']!r}"
    )
    assert doc["run"]["llm_model"] in readme_text, (
        f"README does not reference llm_model={doc['run']['llm_model']!r}"
    )


@_skip_no_published
@_skip_no_readme
def test_readme_headline_results_match_published_aggregates():
    """README's RESULTS_TABLE values must match published/comparison.json
    aggregates. Stable contract between File 11 (this test) and File 12
    (README): numbers are machine-parsed from a sentinel-delimited
    markdown table. See _HEADLINE_METRICS + _tolerance_for for the
    required rows and precision."""
    doc = _load_published()
    readme_text = README_PATH.read_text(encoding="utf-8")
    table = _extract_readme_results_table(readme_text)

    for metric in _HEADLINE_METRICS:
        for side in ("ncp", "llm"):
            aggregate_val = doc["aggregates"][side].get(metric)
            readme_cell = table.get(metric, {}).get(side)
            assert readme_cell is not None, (
                f"README RESULTS_TABLE missing cell: "
                f"metric={metric!r}, side={side!r}"
            )
            parsed = _parse_metric_cell(readme_cell)
            if aggregate_val is None:
                assert parsed is None, (
                    f"{metric}/{side}: README shows {readme_cell!r} but "
                    f"comparison.json aggregate is null"
                )
                continue
            assert parsed is not None, (
                f"{metric}/{side}: README cell {readme_cell!r} parses as "
                f"null but comparison.json has non-null {aggregate_val!r}"
            )
            tol = _tolerance_for(metric)
            if tol == 0.0:
                assert parsed == aggregate_val, (
                    f"{metric}/{side} mismatch: "
                    f"README={parsed!r}, comparison.json={aggregate_val!r}"
                )
            else:
                assert abs(float(parsed) - float(aggregate_val)) <= tol, (
                    f"{metric}/{side} mismatch: "
                    f"README={parsed}, comparison.json={aggregate_val}, "
                    f"tolerance={tol}"
                )
