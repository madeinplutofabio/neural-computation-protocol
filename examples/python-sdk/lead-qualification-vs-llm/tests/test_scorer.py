# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Adversarial tests for scorer.py.

Per CONTRACT.md §14 pre-merge check: wrong classifications, missing
binaries, API outages, and invalid-input rejections on otherwise-valid
fixtures must NOT receive correctness credit. These tests pin that
behavior so later implementation changes cannot silently inflate scores.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# Make scorer.py importable from the example's root directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scorer import (  # noqa: E402  (sys.path mutation must precede import)
    ImplResult,
    aggregate,
    aggregate_by_category,
    has_observable_outcome,
    score_case,
)


# --- Fixture helpers --------------------------------------------------------


def biz_fixture(id_: str, expected: str, category: str = "catalog_full_match") -> dict[str, Any]:
    """Business-outcome fixture (expected.business_outcome set)."""
    return {
        "id": id_,
        "input": {
            "company_url": "https://example.com",
            "target_icp": "test",
            "notes": None,
        },
        "expected": {"business_outcome": expected},
        "category": category,
        "notes_for_reviewer": "synthetic test fixture",
    }


def val_fixture(id_: str, category: str = "invalid_input") -> dict[str, Any]:
    """Invalid-input fixture (expected.validation set)."""
    return {
        "id": id_,
        "input": {
            "company_url": "https://exämple.com",
            "target_icp": "test",
            "notes": None,
        },
        "expected": {"validation": "invalid_input"},
        "category": category,
        "notes_for_reviewer": "synthetic test fixture",
    }


# --- §6.1 ImplResult invariants ---------------------------------------------


def test_implresult_rejects_both_validation_and_business_set():
    with pytest.raises(ValueError, match=r"§6\.1"):
        ImplResult(runtime="success", validation="invalid_input", business="qualified")


def test_implresult_allows_validation_only():
    r = ImplResult(runtime="failure", validation="invalid_input", business=None)
    assert r.validation == "invalid_input"
    assert r.business is None


def test_implresult_allows_business_only():
    r = ImplResult(runtime="success", validation=None, business="qualified")
    assert r.business == "qualified"
    assert r.validation is None


def test_implresult_allows_both_none_for_unresolved():
    r = ImplResult(runtime="unresolved", validation=None, business=None)
    assert r.runtime == "unresolved"
    assert r.validation is None
    assert r.business is None


def test_unresolved_cannot_claim_invalid_input():
    with pytest.raises(ValueError, match=r"unresolved"):
        ImplResult(runtime="unresolved", validation="invalid_input", business=None)


def test_unresolved_cannot_claim_business_result():
    with pytest.raises(ValueError, match=r"unresolved"):
        ImplResult(runtime="unresolved", validation=None, business="qualified")


def test_failure_cannot_claim_business_result():
    with pytest.raises(ValueError, match=r"requires runtime='success'"):
        ImplResult(runtime="failure", validation=None, business="qualified")


@pytest.mark.parametrize(
    "kwargs,match",
    [
        (
            {"runtime": "badruntime", "validation": None, "business": None},
            r"Invalid runtime value",
        ),
        (
            {"runtime": "success", "validation": "wrong_val", "business": None},
            r"Invalid validation value",
        ),
        (
            {"runtime": "success", "validation": None, "business": "wrong_biz"},
            r"Invalid business value",
        ),
    ],
)
def test_implresult_rejects_values_outside_frozen_vocabulary(
    kwargs: dict[str, Any], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        ImplResult(**kwargs)


# --- Business branch of §8.1 ------------------------------------------------


def test_correct_business_classification_scores_true():
    fx = biz_fixture("c1", "qualified")
    r = ImplResult(runtime="success", validation=None, business="qualified")
    assert score_case(fx, r)["correct"] is True


def test_wrong_business_label_scores_false():
    fx = biz_fixture("c2", "qualified")
    r = ImplResult(runtime="success", validation=None, business="not_qualified")
    assert score_case(fx, r)["correct"] is False


def test_business_runtime_failure_without_validation_scores_false():
    # Any non-success runtime (with both axes null) on a business fixture
    # is incorrect — the implementation produced no classification.
    fx = biz_fixture("c3", "qualified")
    r = ImplResult(runtime="failure", validation=None, business=None)
    assert score_case(fx, r)["correct"] is False


def test_business_runtime_unresolved_scores_false():
    fx = biz_fixture("c4", "qualified")
    r = ImplResult(runtime="unresolved", validation=None, business=None)
    assert score_case(fx, r)["correct"] is False


def test_business_null_business_scores_false_even_on_success_runtime():
    # Shouldn't happen in practice (success implies the adapter extracted
    # a label) but if an adapter emits runtime=success with business=null,
    # scorer rejects.
    fx = biz_fixture("c5", "qualified")
    r = ImplResult(runtime="success", validation=None, business=None)
    assert score_case(fx, r)["correct"] is False


def test_validation_rejection_on_business_fixture_scores_false():
    # NCP Failure(INVALID_INPUT) on a well-formed business fixture is
    # a WRONG rejection — the implementation treated valid input as
    # invalid. Must not credit.
    fx = biz_fixture("c6", "qualified")
    r = ImplResult(runtime="failure", validation="invalid_input", business=None)
    assert score_case(fx, r)["correct"] is False


# --- Validation branch of §8.1 ---------------------------------------------


def test_ncp_failure_invalid_input_on_invalid_fixture_scores_true():
    # Canonical correct rejection: terminal result_type=Failure AND
    # error_class=INVALID_INPUT. runtime==failure is NOT disqualifying.
    fx = val_fixture("v1")
    r = ImplResult(runtime="failure", validation="invalid_input", business=None)
    out = score_case(fx, r)
    assert out["correct"] is True
    assert out["runtime"] == "failure"


def test_llm_invalid_input_label_on_invalid_fixture_scores_true():
    # LLM returned 2xx with the "invalid_input" label.
    fx = val_fixture("v2")
    r = ImplResult(runtime="success", validation="invalid_input", business=None)
    assert score_case(fx, r)["correct"] is True


def test_api_outage_on_invalid_fixture_scores_false():
    # Infrastructure failure (unresolved) on an invalid_input fixture.
    # Must NOT count as a correct rejection — the system never examined
    # the input.
    fx = val_fixture("v3")
    r = ImplResult(runtime="unresolved", validation=None, business=None)
    assert score_case(fx, r)["correct"] is False


def test_missing_binary_on_invalid_fixture_scores_false():
    # Same semantics: unresolved for a different infra reason.
    fx = val_fixture("v4")
    r = ImplResult(runtime="unresolved", validation=None, business=None)
    assert score_case(fx, r)["correct"] is False


def test_other_failure_on_invalid_fixture_scores_false():
    # runtime=failure but WITHOUT INVALID_INPUT (e.g., some other NCP
    # error_class, or an LLM refusal). validation is null → not a
    # correct rejection.
    fx = val_fixture("v5")
    r = ImplResult(runtime="failure", validation=None, business=None)
    assert score_case(fx, r)["correct"] is False


def test_business_classification_on_invalid_fixture_scores_false():
    # The LLM emitted a business label on an invalid-input fixture.
    # Expected key is "validation", not "business_outcome" — mismatch.
    fx = val_fixture("v6")
    r = ImplResult(runtime="success", validation=None, business="qualified")
    assert score_case(fx, r)["correct"] is False


# --- Observable-outcome predicate ------------------------------------------


def test_observable_success_with_business():
    assert has_observable_outcome(
        ImplResult(runtime="success", validation=None, business="qualified")
    )


def test_observable_failure_with_invalid_input():
    assert has_observable_outcome(
        ImplResult(runtime="failure", validation="invalid_input", business=None)
    )


def test_observable_llm_success_with_invalid_input():
    # LLM returned 2xx and the label was invalid_input.
    assert has_observable_outcome(
        ImplResult(runtime="success", validation="invalid_input", business=None)
    )


def test_not_observable_unresolved():
    assert not has_observable_outcome(
        ImplResult(runtime="unresolved", validation=None, business=None)
    )


def test_not_observable_failure_without_validation():
    # runtime=failure for some non-INVALID_INPUT reason is NOT observable.
    assert not has_observable_outcome(
        ImplResult(runtime="failure", validation=None, business=None)
    )


# --- Aggregates: §8.2 denominators -----------------------------------------


def test_aggregate_perfect_run():
    scored = [
        score_case(
            biz_fixture(f"p{i}", "qualified"),
            ImplResult(runtime="success", validation=None, business="qualified"),
        )
        for i in range(10)
    ]
    agg = aggregate(scored)
    assert agg["total"] == 10
    assert agg["correct_all_attempted"] == 10
    assert agg["accuracy_all_attempted"] == 1.0
    assert agg["completed"] == 10
    assert agg["correct_completed"] == 10
    assert agg["accuracy_completed_only"] == 1.0
    assert agg["completion_rate"] == 1.0


def test_aggregate_all_wrong_but_completed():
    scored = [
        score_case(
            biz_fixture(f"w{i}", "qualified"),
            ImplResult(runtime="success", validation=None, business="not_qualified"),
        )
        for i in range(10)
    ]
    agg = aggregate(scored)
    assert agg["accuracy_all_attempted"] == 0.0
    assert agg["completed"] == 10
    assert agg["accuracy_completed_only"] == 0.0
    assert agg["completion_rate"] == 1.0


def test_aggregate_frequent_failures_cannot_fake_high_accuracy():
    # 2 correct completions + 8 infrastructure failures.
    # all-attempted = 2/10 = 20% (the honest number).
    # completed-only = 2/2 = 100% (would be misleading in isolation; §8.2
    # requires both to be reported side by side).
    cases = []
    for i in range(2):
        cases.append(
            score_case(
                biz_fixture(f"f{i}", "qualified"),
                ImplResult(runtime="success", validation=None, business="qualified"),
            )
        )
    for i in range(8):
        cases.append(
            score_case(
                biz_fixture(f"f{2 + i}", "qualified"),
                ImplResult(runtime="unresolved", validation=None, business=None),
            )
        )
    agg = aggregate(cases)
    assert agg["accuracy_all_attempted"] == 0.2
    assert agg["accuracy_completed_only"] == 1.0
    assert agg["completion_rate"] == 0.2


def test_aggregate_zero_completed_returns_null_accuracy():
    # §8.2 defined-null case: zero observable → completed-only accuracy
    # is None, NOT 0.0 (semantically different).
    scored = [
        score_case(
            biz_fixture(f"z{i}", "qualified"),
            ImplResult(runtime="unresolved", validation=None, business=None),
        )
        for i in range(5)
    ]
    agg = aggregate(scored)
    assert agg["completed"] == 0
    assert agg["accuracy_completed_only"] is None
    assert agg["completion_rate"] == 0.0
    assert agg["accuracy_all_attempted"] == 0.0


def test_aggregate_empty_list():
    agg = aggregate([])
    assert agg["total"] == 0
    assert agg["accuracy_all_attempted"] == 0.0
    assert agg["accuracy_completed_only"] is None
    assert agg["completion_rate"] == 0.0


def test_aggregate_mixed_business_and_validation_outcomes():
    cases = []
    for i in range(3):
        cases.append(
            score_case(
                biz_fixture(f"mv{i}", "qualified"),
                ImplResult(runtime="success", validation=None, business="qualified"),
            )
        )
    for i in range(2):
        cases.append(
            score_case(
                val_fixture(f"mv{3 + i}"),
                ImplResult(runtime="failure", validation="invalid_input", business=None),
            )
        )
    agg = aggregate(cases)
    assert agg["total"] == 5
    assert agg["correct_all_attempted"] == 5
    assert agg["accuracy_all_attempted"] == 1.0
    assert agg["completed"] == 5
    assert agg["accuracy_completed_only"] == 1.0
    assert agg["completion_rate"] == 1.0


def test_aggregate_api_outage_on_invalid_fixtures_contributes_zero_correct():
    # Adversarial: 5 invalid-input fixtures where the LLM API was down
    # (unresolved). The system never saw the inputs; it must NOT get
    # credit for "rejecting" them.
    scored = [
        score_case(
            val_fixture(f"out{i}"),
            ImplResult(runtime="unresolved", validation=None, business=None),
        )
        for i in range(5)
    ]
    agg = aggregate(scored)
    assert agg["correct_all_attempted"] == 0
    assert agg["accuracy_all_attempted"] == 0.0
    assert agg["completed"] == 0
    assert agg["accuracy_completed_only"] is None


# --- Per-category aggregation (§8.3) ---------------------------------------


def test_aggregate_by_category_separates_buckets():
    scored = [
        score_case(
            biz_fixture("a1", "qualified", category="catalog_full_match"),
            ImplResult(runtime="success", validation=None, business="qualified"),
        ),
        score_case(
            biz_fixture("a2", "qualified", category="catalog_full_match"),
            ImplResult(runtime="success", validation=None, business="not_qualified"),
        ),
        score_case(
            biz_fixture("a3", "needs_enrichment", category="unknown_domain"),
            ImplResult(runtime="success", validation=None, business="needs_enrichment"),
        ),
        score_case(
            val_fixture("a4"),
            ImplResult(runtime="failure", validation="invalid_input", business=None),
        ),
        score_case(
            val_fixture("a5"),
            ImplResult(runtime="unresolved", validation=None, business=None),
        ),
    ]
    by_cat = aggregate_by_category(scored)
    assert set(by_cat.keys()) == {
        "catalog_full_match",
        "unknown_domain",
        "invalid_input",
    }
    full = by_cat["catalog_full_match"]
    assert full["total"] == 2
    assert full["correct_all_attempted"] == 1
    assert full["accuracy_all_attempted"] == 0.5
    unknown = by_cat["unknown_domain"]
    assert unknown["total"] == 1
    assert unknown["correct_all_attempted"] == 1
    invalid = by_cat["invalid_input"]
    assert invalid["total"] == 2
    assert invalid["correct_all_attempted"] == 1
    assert invalid["completed"] == 1
    assert invalid["accuracy_completed_only"] == 1.0


# --- Fabrication safeguards ------------------------------------------------


def test_scored_case_contains_no_measurement_fields():
    scored = score_case(
        biz_fixture("m1", "qualified"),
        ImplResult(runtime="success", validation=None, business="qualified"),
    )
    forbidden_keys = {
        "tokens_in",
        "tokens_out",
        "model_cost_usd",
        "latency_ms",
        "model_api_calls",
        "graph_invocations",
    }
    assert forbidden_keys.isdisjoint(scored.keys()), (
        f"scorer leaked measurement fields: {forbidden_keys & scored.keys()}"
    )


def test_aggregates_contain_no_measurement_fields():
    scored = [
        score_case(
            biz_fixture("mg1", "qualified"),
            ImplResult(runtime="success", validation=None, business="qualified"),
        )
    ]
    agg = aggregate(scored)
    forbidden_keys = {
        "tokens_in",
        "tokens_out",
        "model_cost_usd",
        "latency_ms",
        "model_api_calls",
        "graph_invocations",
    }
    assert forbidden_keys.isdisjoint(agg.keys()), (
        f"aggregator leaked measurement fields: {forbidden_keys & agg.keys()}"
    )
