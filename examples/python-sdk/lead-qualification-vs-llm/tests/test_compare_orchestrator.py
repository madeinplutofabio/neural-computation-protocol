# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Tests for compare.py.

Pins the five Drop-in B requirements:
  1. Fair input handling (independent copies, same input)
  2. Dataset integrity (count, IDs, categories, hash)
  3. Honest aggregation (null-propagation, measured subtotals)
  4. Reproducible run metadata
  5. Explicit execution + publication controls

Plus strict publication validation (identity, case ID, ground truth,
result integrity, aggregate consistency) and legitimate-failure
publishability.
"""

from __future__ import annotations

import argparse
import copy
import re
import sys
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import compare  # noqa: E402
from scorer import AdapterResult, ImplResult  # noqa: E402


# --- Helpers ---------------------------------------------------------------


def _args(
    *,
    run_llm: bool = False,
    skip_ncp: bool = False,
    publish: bool = False,
    ncp_binary: str | None = None,
    fixture_set_hash: str | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        run_llm=run_llm,
        skip_ncp=skip_ncp,
        publish=publish,
        ncp_binary=ncp_binary,
        fixture_set_hash=fixture_set_hash,
    )


def _adapter_result(
    *,
    runtime: str = "success",
    validation: str | None = None,
    business: str | None = "qualified",
    latency_ms: float = 100.0,
    model_api_calls: int = 0,
    graph_invocations: int = 1,
    tokens_in: int | None = 0,
    tokens_out: int | None = 0,
    model_cost_usd: float | None = 0.0,
    llm_model_resolved: str | None = None,
    error: str | None = None,
) -> AdapterResult:
    return AdapterResult(
        impl_result=ImplResult(
            runtime=runtime,  # type: ignore[arg-type]
            validation=validation,  # type: ignore[arg-type]
            business=business,  # type: ignore[arg-type]
        ),
        latency_ms=latency_ms,
        model_api_calls=model_api_calls,
        graph_invocations=graph_invocations,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        model_cost_usd=model_cost_usd,
        llm_model_resolved=llm_model_resolved,
        error=error,
    )


def _mk_ncp_runner_for_expected(fixtures: list[dict[str, Any]]):
    expected_map = {f["id"]: f["expected"] for f in fixtures}
    invocations: list[dict[str, Any]] = []

    def runner(input_dict, *, repo_root, binary_override):
        matched = next((f for f in fixtures if f["input"] == input_dict), None)
        assert matched is not None, f"no fixture matches input {input_dict!r}"
        expected = expected_map[matched["id"]]
        invocations.append({"id": matched["id"], "input": copy.deepcopy(input_dict)})
        if "validation" in expected:
            return _adapter_result(
                runtime="failure",
                validation="invalid_input",
                business=None,
                graph_invocations=1,
            )
        return _adapter_result(
            runtime="success",
            validation=None,
            business=expected["business_outcome"],
            graph_invocations=1,
        )

    runner.invocations = invocations  # type: ignore[attr-defined]
    return runner


def _mk_llm_runner_for_expected(fixtures: list[dict[str, Any]]):
    expected_map = {f["id"]: f["expected"] for f in fixtures}
    invocations: list[dict[str, Any]] = []

    def runner(input_dict, *, client, pricing):
        matched = next((f for f in fixtures if f["input"] == input_dict), None)
        assert matched is not None, f"no fixture matches input {input_dict!r}"
        expected = expected_map[matched["id"]]
        invocations.append({"id": matched["id"], "input": copy.deepcopy(input_dict)})
        if "validation" in expected:
            return _adapter_result(
                runtime="success",
                validation="invalid_input",
                business=None,
                model_api_calls=1,
                graph_invocations=0,
                tokens_in=200,
                tokens_out=5,
                model_cost_usd=(200 / 1e6) * 0.10 + (5 / 1e6) * 0.50,
                llm_model_resolved="claude-haiku-5-5-20261001",
            )
        return _adapter_result(
            runtime="success",
            validation=None,
            business=expected["business_outcome"],
            model_api_calls=1,
            graph_invocations=0,
            tokens_in=200,
            tokens_out=5,
            model_cost_usd=(200 / 1e6) * 0.10 + (5 / 1e6) * 0.50,
            llm_model_resolved="claude-haiku-5-5-20261001",
        )

    runner.invocations = invocations  # type: ignore[attr-defined]
    return runner


def _valid_published_doc() -> dict[str, Any]:
    """Produce a valid publication-ready comparison via run_comparison with
    perfect-score fake adapters. This is the baseline that passes
    _validate_publication_ready untouched; mutation tests derive from it."""
    fixtures = compare.load_fixtures()
    return compare.run_comparison(
        _args(run_llm=True),
        ncp_runner=_mk_ncp_runner_for_expected(fixtures),
        llm_runner=_mk_llm_runner_for_expected(fixtures),
        anthropic_client=Mock(),
        pricing=Mock(input_price_per_mtok=0.1, output_price_per_mtok=0.5),
    )


# --- Requirement 2: Dataset integrity -------------------------------------


def test_load_fixtures_returns_50():
    assert len(compare.load_fixtures()) == 50


def test_validate_fixture_set_accepts_frozen_set():
    fixtures = compare.load_fixtures()
    h = compare.validate_fixture_set(fixtures)
    assert isinstance(h, str) and len(h) == 64


def test_validate_fixture_set_rejects_wrong_count():
    fixtures = compare.load_fixtures()[:40]
    with pytest.raises(ValueError, match=r"expected 50 fixtures, got 40"):
        compare.validate_fixture_set(fixtures)


def test_validate_fixture_set_rejects_duplicate_ids():
    fixtures = compare.load_fixtures()
    fixtures[1]["id"] = fixtures[0]["id"]
    with pytest.raises(ValueError, match=r"duplicate fixture IDs"):
        compare.validate_fixture_set(fixtures)


def test_validate_fixture_set_rejects_category_distribution_mismatch():
    fixtures = compare.load_fixtures()
    fixtures[0]["category"] = "unknown_domain"
    with pytest.raises(ValueError, match=r"category distribution mismatch"):
        compare.validate_fixture_set(fixtures)


def test_validate_fixture_set_rejects_altered_content():
    """Count/IDs/categories unchanged, content changed → committed hash mismatch."""
    fixtures = compare.load_fixtures()
    fixtures[0]["input"]["company_url"] = "https://silently-changed.example.com"
    with pytest.raises(ValueError, match=r"do not match committed"):
        compare.validate_fixture_set(fixtures)


def test_cli_hash_mismatch_against_committed_rejects():
    fixtures = compare.load_fixtures()
    with pytest.raises(ValueError, match=r"--fixture-set-hash does not match committed"):
        compare.validate_fixture_set(fixtures, expected_hash="0" * 64)


def test_fixture_set_hash_is_order_independent():
    fixtures = compare.load_fixtures()
    shuffled = list(reversed(fixtures))
    assert compare.compute_fixture_set_hash(fixtures) == compare.compute_fixture_set_hash(shuffled)


def test_frozen_fixture_hash_file_matches_fixtures():
    fixtures = compare.load_fixtures()
    computed = compare.compute_fixture_set_hash(fixtures)
    committed = compare.FIXTURE_SET_HASH_PATH.read_text(encoding="utf-8").strip()
    assert computed == committed


# --- Prompt-template hash guard -------------------------------------------


def test_load_prompt_template_hash_verifies_file_bytes():
    h = compare.load_prompt_template_hash()
    assert isinstance(h, str) and len(h) == 64


def test_load_prompt_template_hash_rejects_mismatched_hash(tmp_path, monkeypatch):
    bad_path = tmp_path / "bad.sha256"
    bad_path.write_text("f" * 64 + "\n")
    monkeypatch.setattr(compare, "PROMPT_TEMPLATE_HASH_PATH", bad_path)
    with pytest.raises(ValueError, match=r"prompt-template\.sha256 does not match"):
        compare.load_prompt_template_hash()


# --- Requirement 1: Fair input handling -----------------------------------


def test_adapters_receive_only_input_not_metadata():
    fixtures = compare.load_fixtures()
    ncp = _mk_ncp_runner_for_expected(fixtures)
    compare.run_comparison(_args(run_llm=False), ncp_runner=ncp)
    for inv in ncp.invocations:  # type: ignore[attr-defined]
        assert set(inv["input"].keys()) == {"company_url", "target_icp", "notes"}


def test_adapters_receive_independent_copies(monkeypatch):
    """Both adapters must receive deep-copied originals; mutations in one
    must not affect the other OR the frozen fixtures. 50 calls per adapter."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    fixtures = compare.load_fixtures()
    ncp_received: list[dict[str, Any]] = []
    llm_received: list[dict[str, Any]] = []

    def ncp_runner(input_dict, *, repo_root, binary_override):
        ncp_received.append(copy.deepcopy(input_dict))
        input_dict["company_url"] = "MUTATED_BY_NCP"
        return _adapter_result(
            runtime="success", validation=None, business="qualified", graph_invocations=1
        )

    def llm_runner(input_dict, *, client, pricing):
        llm_received.append(copy.deepcopy(input_dict))
        input_dict["company_url"] = "MUTATED_BY_LLM"
        return _adapter_result(
            runtime="success",
            validation=None,
            business="qualified",
            model_api_calls=1,
            graph_invocations=0,
            tokens_in=1,
            tokens_out=1,
            model_cost_usd=0.0,
            llm_model_resolved="x",
        )

    compare.run_comparison(
        _args(run_llm=True),
        ncp_runner=ncp_runner,
        llm_runner=llm_runner,
        anthropic_client=Mock(),
        pricing=Mock(input_price_per_mtok=0.1, output_price_per_mtok=0.5),
    )
    assert len(ncp_received) == 50
    assert len(llm_received) == 50
    for i, fixture in enumerate(fixtures):
        assert ncp_received[i] == fixture["input"], (
            f"NCP input mismatch at i={i}, id={fixture['id']}: got {ncp_received[i]!r}"
        )
        assert llm_received[i] == fixture["input"], (
            f"LLM input mismatch at i={i}, id={fixture['id']}: got {llm_received[i]!r}"
        )
    for ncp_inv, llm_inv in zip(ncp_received, llm_received, strict=True):
        assert ncp_inv == llm_inv
    reloaded = compare.load_fixtures()
    for i, fixture in enumerate(fixtures):
        assert fixture["input"] == reloaded[i]["input"]


# --- Default behavior: NCP only, no API key -------------------------------


def test_default_run_skips_llm_and_runs_ncp():
    fixtures = compare.load_fixtures()
    ncp = _mk_ncp_runner_for_expected(fixtures)
    comparison = compare.run_comparison(_args(), ncp_runner=ncp)
    assert comparison["aggregates"]["ncp"] is not None
    assert comparison["aggregates"]["llm"] is None
    assert len(ncp.invocations) == 50  # type: ignore[attr-defined]


def test_default_run_does_not_require_anthropic_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    fixtures = compare.load_fixtures()
    ncp = _mk_ncp_runner_for_expected(fixtures)
    comparison = compare.run_comparison(_args(run_llm=False), ncp_runner=ncp)
    assert comparison["aggregates"]["llm"] is None


# --- Requirement 3: Honest aggregation (null-propagation) -----------------


def test_aggregates_null_tokens_when_any_case_missing_usage(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    fixtures = compare.load_fixtures()

    def partial_llm_runner(input_dict, *, client, pricing):
        if input_dict["company_url"] == "https://example.com":
            return _adapter_result(
                runtime="success",
                validation=None,
                business="qualified",
                model_api_calls=1,
                graph_invocations=0,
                tokens_in=None,
                tokens_out=None,
                model_cost_usd=None,
            )
        return _adapter_result(
            runtime="success",
            validation=None,
            business="qualified",
            model_api_calls=1,
            graph_invocations=0,
            tokens_in=100,
            tokens_out=5,
            model_cost_usd=(100 / 1e6) * 0.10 + (5 / 1e6) * 0.50,
        )

    comparison = compare.run_comparison(
        _args(run_llm=True),
        ncp_runner=_mk_ncp_runner_for_expected(fixtures),
        llm_runner=partial_llm_runner,
        anthropic_client=Mock(),
        pricing=Mock(input_price_per_mtok=0.1, output_price_per_mtok=0.5),
    )
    llm_agg = comparison["aggregates"]["llm"]
    assert llm_agg["total_tokens_in"] is None
    assert llm_agg["total_tokens_out"] is None
    assert llm_agg["total_model_cost_usd"] is None
    sub = llm_agg["measured_subtotals"]
    assert sub["cases_with_complete_usage_record"] > 0
    assert sub["cases_without_complete_usage_record"] > 0


def test_aggregates_numeric_totals_when_all_usage_known(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    fixtures = compare.load_fixtures()
    comparison = compare.run_comparison(
        _args(run_llm=True),
        ncp_runner=_mk_ncp_runner_for_expected(fixtures),
        llm_runner=_mk_llm_runner_for_expected(fixtures),
        anthropic_client=Mock(),
        pricing=Mock(input_price_per_mtok=0.1, output_price_per_mtok=0.5),
    )
    llm_agg = comparison["aggregates"]["llm"]
    assert llm_agg["total_tokens_in"] == 50 * 200
    assert llm_agg["total_tokens_out"] == 50 * 5


def test_measured_subtotals_count_complete_records(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    fixtures = compare.load_fixtures()

    def mixed_llm(input_dict, *, client, pricing):
        if input_dict["company_url"] == "https://example.com":
            return _adapter_result(
                runtime="success", validation=None, business="qualified",
                model_api_calls=1, graph_invocations=0,
                tokens_in=100, tokens_out=None, model_cost_usd=None,
                llm_model_resolved="x",
            )
        return _adapter_result(
            runtime="success", validation=None, business="qualified",
            model_api_calls=1, graph_invocations=0,
            tokens_in=100, tokens_out=5, model_cost_usd=0.00001005,
            llm_model_resolved="x",
        )

    comparison = compare.run_comparison(
        _args(run_llm=True),
        ncp_runner=_mk_ncp_runner_for_expected(fixtures),
        llm_runner=mixed_llm,
        anthropic_client=Mock(),
        pricing=Mock(input_price_per_mtok=0.1, output_price_per_mtok=0.5),
    )
    sub = comparison["aggregates"]["llm"]["measured_subtotals"]
    assert "cases_with_complete_usage_record" in sub
    assert "cases_without_complete_usage_record" in sub
    assert sub["cases_with_complete_usage_record"] + sub["cases_without_complete_usage_record"] == 50
    assert sub["cases_without_complete_usage_record"] >= 1


# --- Scorer frozen-expected-label verification ---------------------------


def test_scorer_uses_frozen_expected_labels():
    fixtures = compare.load_fixtures()
    comparison = compare.run_comparison(
        _args(), ncp_runner=_mk_ncp_runner_for_expected(fixtures)
    )
    assert comparison["aggregates"]["ncp"]["accuracy_all_attempted"] == 1.0


def test_failures_cannot_inflate_accuracy():
    def failing_runner(input_dict, *, repo_root, binary_override):
        return _adapter_result(
            runtime="unresolved", validation=None, business=None, graph_invocations=0
        )

    comparison = compare.run_comparison(_args(), ncp_runner=failing_runner)
    assert comparison["aggregates"]["ncp"]["accuracy_all_attempted"] == 0.0
    assert comparison["aggregates"]["ncp"]["accuracy_completed_only"] is None


# --- Requirement 4: Reproducible run metadata -----------------------------


def test_run_metadata_has_required_fields():
    fixtures = compare.load_fixtures()
    comparison = compare.run_comparison(
        _args(), ncp_runner=_mk_ncp_runner_for_expected(fixtures)
    )
    run = comparison["run"]
    for key in (
        "timestamp_utc",
        "platform",
        "ncp_binary_version",
        "supported_runtime_versions",
        "ncp_runtime_sdk_version",
        "anthropic_sdk_version",
        "llm_model",
        "llm_model_resolved",
        "pricing_date",
        "fixture_set_revision",
        "fixture_set_sha256",
        "prompt_template_sha256",
    ):
        assert key in run, f"missing run metadata field: {key}"
    assert run["fixture_set_revision"] == "fixtures-rev-01"
    assert run["llm_model"] == "claude-haiku-5-5"


def test_ncp_binary_version_is_not_substituted_from_sdk_constant():
    fixtures = compare.load_fixtures()
    comparison = compare.run_comparison(
        _args(), ncp_runner=_mk_ncp_runner_for_expected(fixtures)
    )
    assert comparison["run"]["ncp_binary_version"] is None
    assert isinstance(comparison["run"]["supported_runtime_versions"], list)
    assert "0.3.6" in comparison["run"]["supported_runtime_versions"]


def test_llm_model_resolved_set_from_first_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    fixtures = compare.load_fixtures()
    comparison = compare.run_comparison(
        _args(run_llm=True),
        ncp_runner=_mk_ncp_runner_for_expected(fixtures),
        llm_runner=_mk_llm_runner_for_expected(fixtures),
        anthropic_client=Mock(),
        pricing=Mock(input_price_per_mtok=0.1, output_price_per_mtok=0.5),
    )
    assert comparison["run"]["llm_model_resolved"] == "claude-haiku-5-5-20261001"


def test_llm_model_resolved_null_when_llm_skipped():
    fixtures = compare.load_fixtures()
    comparison = compare.run_comparison(
        _args(), ncp_runner=_mk_ncp_runner_for_expected(fixtures)
    )
    assert comparison["run"]["llm_model_resolved"] is None


# --- Requirement 5: Execution + publication CLI controls -----------------


def test_cli_main_run_llm_without_api_key_fails(monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    rc = compare.main(["--run-llm"])
    assert rc == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_cli_main_publish_without_llm_fails(monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    rc = compare.main(["--publish"])
    assert rc == 2
    assert "--publish requires" in capsys.readouterr().err


# --- Collision-resistant local filenames ---------------------------------


def test_local_output_filename_uses_microsecond_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "EXAMPLE_DIR", tmp_path)
    comparison = {"schema_version": "x", "run": {}, "per_case": [], "aggregates": {}}
    out_path = compare.write_comparison(comparison, _args())
    assert re.match(r"comparison-\d{8}T\d{6}\.\d{6}Z\.json$", out_path.name)


# --- Nearest-rank percentile ---------------------------------------------


def test_percentile_nearest_rank_is_deterministic():
    values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    assert compare._percentile(values, 50) == 50.0
    assert compare._percentile(values, 95) == 100.0


def test_percentile_empty_returns_none():
    assert compare._percentile([], 50) is None
    assert compare._percentile([], 95) is None


def test_percentile_single_value():
    assert compare._percentile([42.0], 50) == 42.0
    assert compare._percentile([42.0], 95) == 42.0


# --- Comparison structure -------------------------------------------------


def test_comparison_structure_matches_contract_shape():
    fixtures = compare.load_fixtures()
    comparison = compare.run_comparison(
        _args(), ncp_runner=_mk_ncp_runner_for_expected(fixtures)
    )
    assert comparison["schema_version"] == "lead-qual-comparison-0.1"
    assert len(comparison["per_case"]) == 50
    for entry in comparison["per_case"]:
        assert set(entry.keys()) >= {"id", "category", "expected", "ncp", "llm"}
        assert entry["ncp"] is not None
        assert entry["llm"] is None
    assert set(comparison["per_category"].keys()) == set(
        compare.EXPECTED_CATEGORY_COUNTS.keys()
    )


# --- Publication validator: structural -----------------------------------


def test_publish_accepts_valid_document_from_runners(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "EXAMPLE_DIR", tmp_path)
    doc = _valid_published_doc()
    out = compare.write_comparison(doc, _args(run_llm=True, publish=True))
    assert out.parent.name == "published"
    assert out.name == "comparison.json"
    assert out.exists()


def test_publish_rejects_wrong_case_count(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "EXAMPLE_DIR", tmp_path)
    doc = _valid_published_doc()
    doc["per_case"] = doc["per_case"][:40]
    with pytest.raises(ValueError, match=r"must contain 50 cases"):
        compare.write_comparison(doc, _args(run_llm=True, publish=True))


def test_publish_rejects_missing_run_metadata_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "EXAMPLE_DIR", tmp_path)
    doc = _valid_published_doc()
    del doc["run"]["fixture_set_sha256"]
    with pytest.raises(ValueError, match=r"missing keys"):
        compare.write_comparison(doc, _args(run_llm=True, publish=True))


# --- Publication validator: dataset identity -----------------------------


def test_publish_rejects_wrong_fixture_set_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "EXAMPLE_DIR", tmp_path)
    doc = _valid_published_doc()
    doc["run"]["fixture_set_revision"] = "fixtures-rev-02"
    with pytest.raises(ValueError, match=r"fixture_set_revision mismatch"):
        compare.write_comparison(doc, _args(run_llm=True, publish=True))


def test_publish_rejects_wrong_fixture_set_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "EXAMPLE_DIR", tmp_path)
    doc = _valid_published_doc()
    doc["run"]["fixture_set_sha256"] = "0" * 64
    with pytest.raises(ValueError, match=r"fixture_set_sha256 does not match verified"):
        compare.write_comparison(doc, _args(run_llm=True, publish=True))


def test_publish_rejects_wrong_prompt_template_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "EXAMPLE_DIR", tmp_path)
    doc = _valid_published_doc()
    doc["run"]["prompt_template_sha256"] = "0" * 64
    with pytest.raises(ValueError, match=r"prompt_template_sha256 does not match verified"):
        compare.write_comparison(doc, _args(run_llm=True, publish=True))


# --- Publication validator: stale committed-hash detection ---------------


def test_publish_rejects_when_committed_fixture_hash_is_stale(tmp_path, monkeypatch):
    """Attack: modify fixtures without updating fixtures-rev-01.sha256.
    Validator catches via validate_fixture_set() in STEP 0."""
    doc = _valid_published_doc()
    stale_hash_path = tmp_path / "fixtures-rev-01.sha256"
    stale_hash_path.write_text("0" * 64 + "\n", encoding="utf-8")
    monkeypatch.setattr(compare, "FIXTURE_SET_HASH_PATH", stale_hash_path)
    monkeypatch.setattr(compare, "EXAMPLE_DIR", tmp_path)
    with pytest.raises(ValueError, match=r"do not match committed"):
        compare.write_comparison(doc, _args(run_llm=True, publish=True))


def test_publish_rejects_when_committed_prompt_hash_is_stale(tmp_path, monkeypatch):
    """Attack: modify prompt-template.txt without updating its .sha256."""
    doc = _valid_published_doc()
    stale_hash_path = tmp_path / "prompt-template.sha256"
    stale_hash_path.write_text("0" * 64 + "\n", encoding="utf-8")
    monkeypatch.setattr(compare, "PROMPT_TEMPLATE_HASH_PATH", stale_hash_path)
    monkeypatch.setattr(compare, "EXAMPLE_DIR", tmp_path)
    with pytest.raises(ValueError, match=r"prompt-template\.sha256 does not match"):
        compare.write_comparison(doc, _args(run_llm=True, publish=True))


# --- Publication validator: case identity --------------------------------


def test_publish_rejects_duplicate_case_id(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "EXAMPLE_DIR", tmp_path)
    doc = _valid_published_doc()
    doc["per_case"][1]["id"] = doc["per_case"][0]["id"]
    with pytest.raises(ValueError, match=r"duplicate IDs"):
        compare.write_comparison(doc, _args(run_llm=True, publish=True))


def test_publish_rejects_unknown_case_id(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "EXAMPLE_DIR", tmp_path)
    doc = _valid_published_doc()
    doc["per_case"][0]["id"] = "lead-9999"
    with pytest.raises(ValueError, match=r"IDs don't match fixtures-rev-01"):
        compare.write_comparison(doc, _args(run_llm=True, publish=True))


# --- Publication validator: ground truth ---------------------------------


def test_publish_rejects_altered_category(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "EXAMPLE_DIR", tmp_path)
    doc = _valid_published_doc()
    doc["per_case"][0]["category"] = "unknown_domain"
    with pytest.raises(ValueError, match=r"category mismatch"):
        compare.write_comparison(doc, _args(run_llm=True, publish=True))


def test_publish_rejects_altered_expected(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "EXAMPLE_DIR", tmp_path)
    doc = _valid_published_doc()
    doc["per_case"][0]["expected"] = {"business_outcome": "not_qualified"}
    with pytest.raises(ValueError, match=r"expected mismatch"):
        compare.write_comparison(doc, _args(run_llm=True, publish=True))


# --- Publication validator: result integrity -----------------------------


def test_publish_rejects_missing_essential_side_field(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "EXAMPLE_DIR", tmp_path)
    doc = _valid_published_doc()
    del doc["per_case"][0]["ncp"]["runtime"]
    with pytest.raises(ValueError, match=r"missing essential fields"):
        compare.write_comparison(doc, _args(run_llm=True, publish=True))


def test_publish_rejects_empty_side_object(tmp_path, monkeypatch):
    """An empty {} is NOT a completed measurement."""
    monkeypatch.setattr(compare, "EXAMPLE_DIR", tmp_path)
    doc = _valid_published_doc()
    doc["per_case"][0]["llm"] = {}
    with pytest.raises(ValueError, match=r"missing essential fields"):
        compare.write_comparison(doc, _args(run_llm=True, publish=True))


def test_publish_rejects_missing_side_in_per_case(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "EXAMPLE_DIR", tmp_path)
    doc = _valid_published_doc()
    doc["per_case"][7]["llm"] = None
    with pytest.raises(ValueError, match=r"missing 'llm' side"):
        compare.write_comparison(doc, _args(run_llm=True, publish=True))


def test_publish_rejects_inconsistent_aggregate_total(tmp_path, monkeypatch):
    """Catches post-generation tampering with aggregate totals."""
    monkeypatch.setattr(compare, "EXAMPLE_DIR", tmp_path)
    doc = _valid_published_doc()
    doc["aggregates"]["llm"]["total_model_api_calls"] = 999
    with pytest.raises(ValueError, match=r"total_model_api_calls mismatch"):
        compare.write_comparison(doc, _args(run_llm=True, publish=True))


# --- Publication validator: per-category coverage ------------------------


def test_publish_rejects_missing_category(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "EXAMPLE_DIR", tmp_path)
    doc = _valid_published_doc()
    del doc["per_category"]["catalog_no_match"]
    with pytest.raises(ValueError, match=r"missing category"):
        compare.write_comparison(doc, _args(run_llm=True, publish=True))


# --- Legitimate failures remain publishable ------------------------------


def test_publish_accepts_document_with_unresolved_case_and_null_usage(
    tmp_path, monkeypatch
):
    """Legitimate operational failures and null usage must remain publishable."""
    monkeypatch.setattr(compare, "EXAMPLE_DIR", tmp_path)
    fixtures = compare.load_fixtures()
    perfect_llm = _mk_llm_runner_for_expected(fixtures)

    def mixed_llm(input_dict, *, client, pricing):
        # Simulate an API timeout on lead-0046 (the non-ASCII company_url).
        if "ä" in input_dict["company_url"]:
            return _adapter_result(
                runtime="unresolved",
                validation=None,
                business=None,
                model_api_calls=1,
                graph_invocations=0,
                tokens_in=None,
                tokens_out=None,
                model_cost_usd=None,
                llm_model_resolved=None,
                error="api_error: APITimeoutError",
            )
        return perfect_llm(input_dict, client=client, pricing=pricing)

    doc = compare.run_comparison(
        _args(run_llm=True),
        ncp_runner=_mk_ncp_runner_for_expected(fixtures),
        llm_runner=mixed_llm,
        anthropic_client=Mock(),
        pricing=Mock(input_price_per_mtok=0.1, output_price_per_mtok=0.5),
    )
    assert doc["aggregates"]["llm"]["total_tokens_in"] is None
    assert doc["aggregates"]["llm"]["total_model_cost_usd"] is None
    assert (
        doc["aggregates"]["llm"]["measured_subtotals"][
            "cases_without_complete_usage_record"
        ]
        >= 1
    )
    out_path = compare.write_comparison(doc, _args(run_llm=True, publish=True))
    assert out_path.exists()


# --- Regression: write_comparison() must emit LF-only line endings ---------
#
# On Windows, Python's text-mode open() converts "\n" to "\r\n" by default.
# A CRLF-contaminated published artifact fails `git diff --check` and is
# non-portable. The writer must open with newline="\n" on both the --publish
# path and the local path. Exercises the real write_comparison() entry
# point, not a helper in isolation.


def test_write_comparison_local_output_uses_lf_only_newlines(tmp_path, monkeypatch):
    monkeypatch.setattr(compare, "EXAMPLE_DIR", tmp_path)
    fixtures = compare.load_fixtures()
    doc = compare.run_comparison(
        _args(),
        ncp_runner=_mk_ncp_runner_for_expected(fixtures),
        llm_runner=lambda inp, *, client, pricing: _adapter_result(),
        anthropic_client=Mock(),
        pricing=Mock(input_price_per_mtok=0.1, output_price_per_mtok=0.5),
    )
    out_path = compare.write_comparison(doc, _args())
    content = out_path.read_bytes()
    assert b"\r\n" not in content, (
        "write_comparison() emitted CRLF; the text-mode open() must pass "
        "newline='\\n' so the output is portable and passes git diff --check"
    )
    assert b"\r" not in content, (
        "write_comparison() emitted lone CR characters"
    )
