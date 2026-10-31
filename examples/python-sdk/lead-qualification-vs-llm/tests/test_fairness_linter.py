# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Fairness linter: consolidated CONTRACT.md §10 invariants.

Each test reuses the production functions from scorer, baseline_llm,
compare, and ncp_version. A drift in any of those implementations
fails here before any measurement run can proceed.

Deterministic, offline, zero API credentials. CI runs this as the
single top-level fairness check before the measurement stage.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import compare  # noqa: E402
import scorer as _scorer_mod  # noqa: E402
from baseline_llm import _VALID_LABELS as LLM_VALID_LABELS  # noqa: E402
from baseline_llm import render_prompt  # noqa: E402


# --- §10 check 3: frozen hashes match actual content ---------------------


def test_fixture_set_hash_committed_file_matches_fixture_content():
    """validate_fixture_set() raises if the committed hash is stale."""
    fixtures = compare.load_fixtures()
    verified = compare.validate_fixture_set(fixtures)
    committed = compare.FIXTURE_SET_HASH_PATH.read_text(encoding="utf-8").strip()
    assert verified == committed


def test_prompt_template_hash_committed_file_matches_template_content():
    """load_prompt_template_hash() raises if the committed hash is stale."""
    h = compare.load_prompt_template_hash()
    assert isinstance(h, str) and len(h) == 64


# --- §10 check 2: prompt-input containment -------------------------------


def test_render_prompt_signature_is_single_parameter():
    """§10 check 2a: the prompt renderer exposes exactly one parameter
    with no default channel for fixture metadata."""
    sig = inspect.signature(render_prompt)
    params = list(sig.parameters.values())
    assert len(params) == 1
    param = params[0]
    assert param.default is inspect.Parameter.empty
    assert param.kind in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.POSITIONAL_ONLY,
    )


def test_render_prompt_metadata_mutation_invariant_over_all_fixtures():
    """§10 check 2b: changing fixture metadata around identical input
    must not alter the rendered prompt. Covers all 50 fixtures with
    multiple alternative metadata mutations each."""
    fixtures = compare.load_fixtures()
    alt_metadata_variants = [
        {
            "id": "lead-9999",
            "expected": {"business_outcome": "qualified"},
            "category": "catalog_full_match",
            "notes_for_reviewer": "alternative note A",
        },
        {
            "id": "lead-0000",
            "expected": {"validation": "invalid_input"},
            "category": "invalid_input",
            "notes_for_reviewer": "alternative note B, very different wording",
        },
    ]
    for fixture in fixtures:
        inp = fixture["input"]
        baseline = render_prompt(inp)
        for alt in alt_metadata_variants:
            synthetic = {"input": inp, **alt}
            assert render_prompt(synthetic["input"]) == baseline, (
                f"metadata mutation changed rendered prompt for fixture "
                f"id={fixture['id']}, alt_metadata={alt['id']}"
            )


# --- §10 check 4: label-vocabulary consistency across scorer + LLM ------


def test_label_vocabulary_is_consistent_across_scorer_and_llm_adapter():
    """The scorer's business ∪ validation vocabulary must equal the
    LLM adapter's allowed output label set."""
    scorer_combined = set(_scorer_mod._VALID_BUSINESSES) | set(
        _scorer_mod._VALID_VALIDATIONS
    )
    llm_vocab = set(LLM_VALID_LABELS)
    assert scorer_combined == llm_vocab, (
        f"label vocabulary drift: "
        f"scorer={sorted(scorer_combined)}, "
        f"baseline_llm={sorted(llm_vocab)}"
    )


def test_runtime_vocabulary_is_the_frozen_three_values():
    """Catches a scorer drift that would silently change what 'success'
    / 'failure' / 'unresolved' mean."""
    assert set(_scorer_mod._VALID_RUNTIMES) == {"success", "failure", "unresolved"}


# --- §10 check 1: both adapters accept the same input contract ---------


def test_adapter_entry_signatures_accept_input_dict_as_first_positional():
    """The orchestrator passes fixture['input'] to both run_ncp and
    run_llm; both must accept it as their first positional parameter."""
    from baseline_llm import run_llm
    from ncp_version import run_ncp

    for fn, name in [(run_ncp, "run_ncp"), (run_llm, "run_llm")]:
        sig = inspect.signature(fn)
        params = list(sig.parameters.values())
        assert len(params) >= 1, f"{name}: missing input_dict parameter"
        first = params[0]
        assert first.name == "input_dict", (
            f"{name}: first parameter is {first.name!r}, expected 'input_dict'"
        )
        assert first.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.POSITIONAL_ONLY,
        ), f"{name}: first parameter must be positional"


# --- CI-safety: no credentials needed -----------------------------------


def test_fairness_linter_requires_no_anthropic_api_key(monkeypatch):
    """The fairness linter is the gate CI runs without any live-API
    credentials."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert len(compare.load_fixtures()) == 50
    assert compare.validate_fixture_set(compare.load_fixtures())


# --- CONTRACT.md §7 shape check -----------------------------------------


def test_every_fixture_has_mutually_exclusive_expected_shape():
    """A fixture's `expected` must have exactly one of
    {business_outcome, validation}. Pins §7."""
    for fixture in compare.load_fixtures():
        exp = fixture["expected"]
        keys = set(exp.keys())
        assert keys in ({"business_outcome"}, {"validation"}), (
            f"fixture id={fixture['id']!r} has invalid expected shape: {exp!r}"
        )
        if "validation" in exp:
            assert exp["validation"] == "invalid_input", (
                f"fixture id={fixture['id']!r} has invalid validation "
                f"value: {exp['validation']!r}"
            )
            assert fixture["category"] == "invalid_input", (
                f"fixture id={fixture['id']!r} has validation expected but "
                f"non-invalid_input category: {fixture['category']!r}"
            )
        else:
            assert exp["business_outcome"] in {
                "qualified",
                "not_qualified",
                "needs_enrichment",
            }, f"fixture id={fixture['id']!r} has invalid business_outcome: {exp!r}"
            assert fixture["category"] != "invalid_input", (
                f"fixture id={fixture['id']!r} has business expected but "
                f"invalid_input category"
            )
