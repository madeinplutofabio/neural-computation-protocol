# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Orchestrator for the lead-qualification-vs-llm comparison.

Loads the frozen fixture set, validates its identity, runs each
fixture through the NCP adapter and/or the LLM adapter, scores every
observable outcome against the frozen expected labels, and writes a
machine-readable comparison.json.

See CONTRACT.md:
  * §7 — fixture schema and dataset identity
  * §8 — correctness evaluation (handed off to scorer.py)
  * §9.1 — LLM baseline configuration
  * §9.4 — comparison.json shape + measurement rules
  * §11 — CI policy and publication controls

Design rules:
  1. Fair input handling: deep-copy fixture["input"] before each
     adapter call so neither can mutate the other's data.
  2. Dataset integrity: frozen 50-fixture set, IDs lead-0001..0050,
     category counts per §7.1, SHA-256 anchored to the committed
     fixtures-rev-01.sha256 file (content-verified).
  3. Honest aggregation: delegated to scorer.aggregate; token/cost
     totals follow §8.5 null-propagation.
  4. Reproducible run metadata: fixture-set hash, prompt-template
     hash, requested + resolved model IDs, pricing anchor date,
     SDK/runtime versions, platform.
  5. Explicit publication control: default run writes
     local/comparison-<ms-ts>.json; --publish writes
     published/comparison.json and requires both sides to have run
     plus a strict _validate_publication_ready() pass.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scorer import (  # noqa: E402
    AdapterResult,
    aggregate,
    aggregate_by_category,
    score_case,
)

# --- Constants -------------------------------------------------------------

EXAMPLE_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = EXAMPLE_DIR / "fixtures"
PROMPT_TEMPLATE_PATH = EXAMPLE_DIR / "prompt-template.txt"
PROMPT_TEMPLATE_HASH_PATH = EXAMPLE_DIR / "prompt-template.sha256"
PRICING_PATH = EXAMPLE_DIR / "pricing.json"
FIXTURE_SET_HASH_PATH = EXAMPLE_DIR / "fixtures-rev-01.sha256"

FIXTURE_SET_REVISION = "fixtures-rev-01"
COMPARISON_SCHEMA_VERSION = "lead-qual-comparison-0.1"
LLM_MODEL = "claude-haiku-5-5"

EXPECTED_CATEGORY_COUNTS: dict[str, int] = {
    "catalog_full_match": 6,
    "catalog_partial_match": 9,
    "catalog_no_match": 6,
    "catalog_uninterpretable_icp": 9,
    "unknown_domain": 15,
    "invalid_input": 5,
}
EXPECTED_FIXTURE_COUNT = sum(EXPECTED_CATEGORY_COUNTS.values())  # 50


# --- Dataset loading + identity ------------------------------------------


def load_fixtures(fixtures_dir: Path | None = None) -> list[dict[str, Any]]:
    """Load all fixtures from <fixtures_dir>/*.json, concatenated.

    Looks up FIXTURES_DIR at call time so monkeypatching for tests is
    effective. Deliberately does NOT shuffle; fixture_set_hash is
    computed after sorting by id so it is order-independent.
    """
    if fixtures_dir is None:
        fixtures_dir = FIXTURES_DIR
    out: list[dict[str, Any]] = []
    for path in sorted(fixtures_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "fixtures" not in data:
            raise ValueError(f"{path.name}: missing 'fixtures' key")
        if not isinstance(data["fixtures"], list):
            raise ValueError(f"{path.name}: 'fixtures' is not a list")
        out.extend(data["fixtures"])
    return out


def compute_fixture_set_hash(fixtures: list[dict[str, Any]]) -> str:
    """Deterministic SHA-256 over the fixture set.

    Sorts by `id`, canonicalizes JSON (sort_keys, no whitespace, UTF-8),
    hashes the bytes. Order-independent across input ordering and
    source-file sharding.
    """
    sorted_fixtures = sorted(fixtures, key=lambda f: f["id"])
    canonical = json.dumps(
        sorted_fixtures,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_fixture_set(
    fixtures: list[dict[str, Any]],
    *,
    expected_hash: str | None = None,
) -> str:
    """Reject anything that doesn't match the frozen fixtures-rev-01 identity.

    Checks, in order:
      1. Count = 50.
      2. IDs cover lead-0001..lead-0050 with no duplicates.
      3. Category distribution matches §7.1.
      4. Computed hash equals the committed <FIXTURE_SET_HASH_PATH>
         contents — the authoritative frozen identity. Any content
         change requires a new revision (fixtures-rev-NN).
      5. If `expected_hash` is also provided (CLI --fixture-set-hash),
         it must equal the committed hash. Defense-in-depth; cannot
         bypass the committed-hash check.

    Returns the committed hash on success.
    """
    if len(fixtures) != EXPECTED_FIXTURE_COUNT:
        raise ValueError(
            f"expected {EXPECTED_FIXTURE_COUNT} fixtures, got {len(fixtures)}"
        )
    ids = [f["id"] for f in fixtures]
    if len(set(ids)) != len(ids):
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(f"duplicate fixture IDs: {duplicates!r}")
    expected_ids = {f"lead-{n:04d}" for n in range(1, EXPECTED_FIXTURE_COUNT + 1)}
    if set(ids) != expected_ids:
        missing = sorted(expected_ids - set(ids))
        extra = sorted(set(ids) - expected_ids)
        raise ValueError(f"fixture ID mismatch: missing={missing!r} extra={extra!r}")
    counts: dict[str, int] = {}
    for f in fixtures:
        counts[f["category"]] = counts.get(f["category"], 0) + 1
    if counts != EXPECTED_CATEGORY_COUNTS:
        raise ValueError(
            f"category distribution mismatch: got {counts!r}, "
            f"expected {EXPECTED_CATEGORY_COUNTS!r}"
        )
    computed_hash = compute_fixture_set_hash(fixtures)
    committed_hash = FIXTURE_SET_HASH_PATH.read_text(encoding="utf-8").strip()
    if computed_hash != committed_hash:
        raise ValueError(
            f"fixtures do not match committed {FIXTURE_SET_REVISION} identity: "
            f"computed={computed_hash}, committed={committed_hash}. "
            f"A content change requires a new revision (fixtures-rev-NN)."
        )
    if expected_hash is not None and expected_hash != committed_hash:
        raise ValueError(
            f"--fixture-set-hash does not match committed {FIXTURE_SET_REVISION}: "
            f"provided={expected_hash}, committed={committed_hash}"
        )
    return committed_hash


# --- Pricing + prompt-template loading -----------------------------------


def load_pricing() -> Any:  # Pricing from baseline_llm
    from baseline_llm import Pricing

    data = json.loads(PRICING_PATH.read_text(encoding="utf-8"))
    return Pricing(
        model=data["model"],
        input_price_per_mtok=data["input_price_per_mtok"],
        output_price_per_mtok=data["output_price_per_mtok"],
        pricing_date=data["pricing_date"],
    )


def load_pricing_date() -> str:
    """Pricing date without importing baseline_llm (CI-safe when
    anthropic isn't installed and the LLM side is skipped)."""
    data = json.loads(PRICING_PATH.read_text(encoding="utf-8"))
    return str(data["pricing_date"])


def load_prompt_template_hash() -> str:
    """Return the committed prompt-template SHA-256, verifying it
    matches the template file's actual bytes."""
    committed = PROMPT_TEMPLATE_HASH_PATH.read_text(encoding="utf-8").strip()
    actual = hashlib.sha256(PROMPT_TEMPLATE_PATH.read_bytes()).hexdigest()
    if committed != actual:
        raise ValueError(
            f"prompt-template.sha256 does not match prompt-template.txt bytes: "
            f"committed={committed}, actual={actual}"
        )
    return committed


# --- Per-side serialization + percentile helper --------------------------


def _side_entry(result: AdapterResult, correct: bool, observable: bool) -> dict[str, Any]:
    """Shape of per_case[i].ncp or per_case[i].llm."""
    impl = result["impl_result"]
    return {
        "runtime": impl.runtime,
        "validation": impl.validation,
        "business": impl.business,
        "correct": correct,
        "observable": observable,
        "latency_ms": result["latency_ms"],
        "model_api_calls": result["model_api_calls"],
        "graph_invocations": result["graph_invocations"],
        "tokens_in": result["tokens_in"],
        "tokens_out": result["tokens_out"],
        "model_cost_usd": result["model_cost_usd"],
        "llm_model_resolved": result["llm_model_resolved"],
        "error": result["error"],
    }


def _percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank empirical percentile.

    Deterministic, no interpolation, no extrapolation. Returns the
    value at index ceil(pct/100 * N) - 1 of the sorted values
    (1-indexed). Returns None for empty input. For a 50-case run,
    p95 returns sorted[47] (ceil(0.95 * 50) = 48, 1-indexed).
    """
    if not values:
        return None
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    rank = max(1, math.ceil(pct / 100.0 * n))
    return sorted_vals[rank - 1]


def _side_aggregates(
    results: list[AdapterResult], scored: list[dict[str, Any]]
) -> dict[str, Any]:
    """Compute per-side aggregates: correctness + measurement totals."""
    corr = aggregate(scored)  # type: ignore[arg-type]
    tokens_in_vals = [r["tokens_in"] for r in results]
    tokens_out_vals = [r["tokens_out"] for r in results]
    cost_vals = [r["model_cost_usd"] for r in results]
    # A "complete usage record" has ALL three fields non-null.
    cases_with_complete = sum(
        1
        for ti, to, co in zip(tokens_in_vals, tokens_out_vals, cost_vals, strict=True)
        if ti is not None and to is not None and co is not None
    )
    cases_without_complete = len(results) - cases_with_complete
    tokens_in_total: int | None = (
        sum(v for v in tokens_in_vals if v is not None)
        if all(v is not None for v in tokens_in_vals)
        else None
    )
    tokens_out_total: int | None = (
        sum(v for v in tokens_out_vals if v is not None)
        if all(v is not None for v in tokens_out_vals)
        else None
    )
    cost_total: float | None = (
        sum(v for v in cost_vals if v is not None)
        if all(v is not None for v in cost_vals)
        else None
    )
    latencies = [r["latency_ms"] for r in results if r["latency_ms"] > 0]
    return {
        "accuracy_all_attempted": corr["accuracy_all_attempted"],
        "accuracy_completed_only": corr["accuracy_completed_only"],
        "completion_rate": corr["completion_rate"],
        "latency_ms_p50": _percentile(latencies, 50),
        "latency_ms_p95": _percentile(latencies, 95),
        "total_model_api_calls": sum(r["model_api_calls"] for r in results),
        "total_graph_invocations": sum(r["graph_invocations"] for r in results),
        "total_tokens_in": tokens_in_total,
        "total_tokens_out": tokens_out_total,
        "total_model_cost_usd": cost_total,
        "measured_subtotals": {
            "cases_with_complete_usage_record": cases_with_complete,
            "cases_without_complete_usage_record": cases_without_complete,
            "partial_tokens_in": sum(v for v in tokens_in_vals if v is not None),
            "partial_tokens_out": sum(v for v in tokens_out_vals if v is not None),
            "partial_model_cost_usd": sum(v for v in cost_vals if v is not None),
        },
    }


# --- Run metadata ---------------------------------------------------------


def _collect_run_metadata(
    *,
    fixture_set_hash: str,
    prompt_hash: str,
    pricing_date: str,
    llm_model_resolved: str | None,
    is_ci: bool,
) -> dict[str, Any]:
    """Return the comparison.json `run` block."""
    from ncp_runtime import SUPPORTED_RUNTIME_VERSIONS

    ncp_runtime_version: str
    try:
        import ncp_runtime as _ncp

        ncp_runtime_version = getattr(_ncp, "__version__", "unknown")
    except ImportError:
        ncp_runtime_version = "not_installed"

    anthropic_version: str
    try:
        import anthropic as _ant

        anthropic_version = getattr(_ant, "__version__", "unknown")
    except ImportError:
        anthropic_version = "not_installed"

    return {
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "platform": {
            "os": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(),
            "ci": is_ci,
        },
        # ncp_binary_version is the ACTUAL running binary's reported
        # version if observed, else null. We do NOT substitute the
        # SDK's SUPPORTED_RUNTIME_VERSIONS as a stand-in — those are
        # different facts (what the SDK accepts vs what actually ran).
        "ncp_binary_version": None,
        "supported_runtime_versions": list(SUPPORTED_RUNTIME_VERSIONS),
        "ncp_runtime_sdk_version": ncp_runtime_version,
        "anthropic_sdk_version": anthropic_version,
        "llm_model": LLM_MODEL,
        "llm_model_resolved": llm_model_resolved,
        "pricing_date": pricing_date,
        "fixture_set_revision": FIXTURE_SET_REVISION,
        "fixture_set_sha256": fixture_set_hash,
        "prompt_template_sha256": prompt_hash,
    }


# --- Orchestration (dependency-injectable) --------------------------------


def _copy_input(input_dict: dict[str, Any]) -> dict[str, Any]:
    """Return a defensive deep copy so adapters cannot share state."""
    return copy.deepcopy(input_dict)


def run_comparison(
    args: argparse.Namespace,
    *,
    repo_root: Path | None = None,
    ncp_runner: Callable[..., AdapterResult] | None = None,
    llm_runner: Callable[..., AdapterResult] | None = None,
    anthropic_client: Any = None,
    pricing: Any = None,
) -> dict[str, Any]:
    """Compute the comparison dict. Does NOT write anything to disk."""
    if repo_root is None:
        repo_root = EXAMPLE_DIR.parent.parent.parent

    # Dataset integrity before any execution.
    fixtures = load_fixtures()
    fixture_set_hash = validate_fixture_set(
        fixtures, expected_hash=args.fixture_set_hash
    )

    # Prompt-template hash verification.
    prompt_hash = load_prompt_template_hash()
    pricing_date = load_pricing_date()
    if pricing is None and args.run_llm:
        pricing = load_pricing()

    # Resolve runners (lazy imports).
    if not args.skip_ncp and ncp_runner is None:
        from ncp_version import run_ncp as _ncp_runner

        ncp_runner = _ncp_runner
    if args.run_llm and llm_runner is None:
        from baseline_llm import run_llm as _llm_runner

        llm_runner = _llm_runner
    if args.run_llm and anthropic_client is None:
        from anthropic import Anthropic

        anthropic_client = Anthropic(max_retries=0)

    ncp_results: list[AdapterResult] = []
    llm_results: list[AdapterResult] = []
    ncp_scored: list[dict[str, Any]] = []
    llm_scored: list[dict[str, Any]] = []
    per_case: list[dict[str, Any]] = []
    llm_model_resolved: str | None = None

    for fixture in fixtures:
        entry: dict[str, Any] = {
            "id": fixture["id"],
            "category": fixture["category"],
            "expected": fixture["expected"],
        }

        if not args.skip_ncp:
            assert ncp_runner is not None
            ncp_input = _copy_input(fixture["input"])
            ncp_result = ncp_runner(
                ncp_input, repo_root=repo_root, binary_override=args.ncp_binary
            )
            ncp_sc = score_case(fixture, ncp_result["impl_result"])
            ncp_results.append(ncp_result)
            ncp_scored.append(dict(ncp_sc))
            entry["ncp"] = _side_entry(ncp_result, ncp_sc["correct"], ncp_sc["observable"])
        else:
            entry["ncp"] = None

        if args.run_llm:
            assert llm_runner is not None
            llm_input = _copy_input(fixture["input"])
            llm_result = llm_runner(
                llm_input, client=anthropic_client, pricing=pricing
            )
            llm_sc = score_case(fixture, llm_result["impl_result"])
            llm_results.append(llm_result)
            llm_scored.append(dict(llm_sc))
            entry["llm"] = _side_entry(llm_result, llm_sc["correct"], llm_sc["observable"])
            if llm_model_resolved is None and llm_result["llm_model_resolved"] is not None:
                llm_model_resolved = llm_result["llm_model_resolved"]
        else:
            entry["llm"] = None

        per_case.append(entry)

    aggregates: dict[str, Any] = {}
    per_category: dict[str, dict[str, Any]] = {}

    if ncp_results:
        aggregates["ncp"] = _side_aggregates(ncp_results, ncp_scored)
        per_category_ncp = aggregate_by_category(ncp_scored)  # type: ignore[arg-type]
    else:
        aggregates["ncp"] = None
        per_category_ncp = {}
    if llm_results:
        aggregates["llm"] = _side_aggregates(llm_results, llm_scored)
        per_category_llm = aggregate_by_category(llm_scored)  # type: ignore[arg-type]
    else:
        aggregates["llm"] = None
        per_category_llm = {}

    all_cats = set(per_category_ncp.keys()) | set(per_category_llm.keys())
    for cat in sorted(all_cats):
        per_category[cat] = {
            "ncp": dict(per_category_ncp[cat]) if cat in per_category_ncp else None,
            "llm": dict(per_category_llm[cat]) if cat in per_category_llm else None,
        }

    is_ci = bool(os.environ.get("CI"))
    run_meta = _collect_run_metadata(
        fixture_set_hash=fixture_set_hash,
        prompt_hash=prompt_hash,
        pricing_date=pricing_date,
        llm_model_resolved=llm_model_resolved,
        is_ci=is_ci,
    )

    return {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "run": run_meta,
        "per_case": per_case,
        "aggregates": aggregates,
        "per_category": per_category,
    }


# --- Publication validation (strict) --------------------------------------


def _validate_publication_ready(comparison: dict[str, Any]) -> None:
    """Strict identity + integrity validation for publication.

    STEP 0: verify committed hash files still match actual fixture +
    template content. validate_fixture_set() raises if committed hash
    is stale; load_prompt_template_hash() raises if template hash is
    stale. The recorded run metadata hashes must then equal these
    verified values.

    Does NOT require perfect classification accuracy or complete LLM
    usage. Legitimate failures (unresolved, Failure, null usage)
    remain publishable.
    """
    # STEP 0 — content-verify committed hashes first.
    frozen_fixtures = load_fixtures()
    verified_fixture_hash = validate_fixture_set(frozen_fixtures)
    verified_prompt_hash = load_prompt_template_hash()

    # STEP 1 — structural shape.
    per_case = comparison.get("per_case")
    if not isinstance(per_case, list) or len(per_case) != EXPECTED_FIXTURE_COUNT:
        raise ValueError(
            f"published comparison must contain {EXPECTED_FIXTURE_COUNT} cases, "
            f"got {len(per_case) if isinstance(per_case, list) else 'invalid'}"
        )

    run = comparison.get("run", {})
    required_run_keys = {
        "timestamp_utc",
        "fixture_set_revision",
        "fixture_set_sha256",
        "prompt_template_sha256",
        "pricing_date",
        "llm_model",
        "ncp_runtime_sdk_version",
    }
    missing_run_keys = required_run_keys - set(run.keys())
    if missing_run_keys:
        raise ValueError(
            f"published run metadata missing keys: {sorted(missing_run_keys)!r}"
        )

    # STEP 2 — dataset identity against VERIFIED hashes.
    if run.get("fixture_set_revision") != FIXTURE_SET_REVISION:
        raise ValueError(
            f"published fixture_set_revision mismatch: "
            f"got {run.get('fixture_set_revision')!r}, "
            f"expected {FIXTURE_SET_REVISION!r}"
        )
    if run.get("fixture_set_sha256") != verified_fixture_hash:
        raise ValueError(
            f"published fixture_set_sha256 does not match verified "
            f"{FIXTURE_SET_REVISION} content hash"
        )
    if run.get("prompt_template_sha256") != verified_prompt_hash:
        raise ValueError(
            "published prompt_template_sha256 does not match verified "
            "prompt-template content hash"
        )

    # STEP 3 — case identity (reuse frozen_fixtures).
    frozen_by_id = {f["id"]: f for f in frozen_fixtures}
    frozen_id_set = set(frozen_by_id.keys())
    published_ids = [entry.get("id") for entry in per_case]
    if len(set(published_ids)) != len(published_ids):
        duplicates = sorted({i for i in published_ids if published_ids.count(i) > 1})
        raise ValueError(f"published duplicate IDs: {duplicates!r}")
    published_id_set = set(published_ids)
    missing_ids = frozen_id_set - published_id_set
    extra_ids = published_id_set - frozen_id_set
    if missing_ids or extra_ids:
        raise ValueError(
            f"published case IDs don't match fixtures-rev-01: "
            f"missing={sorted(missing_ids)!r} extra={sorted(extra_ids)!r}"
        )

    # STEP 4 — ground truth: category + expected per case.
    for entry in per_case:
        case_id = entry.get("id")
        frozen = frozen_by_id[case_id]
        if entry.get("category") != frozen["category"]:
            raise ValueError(
                f"published case id={case_id!r} category mismatch: "
                f"published={entry.get('category')!r}, "
                f"frozen={frozen['category']!r}"
            )
        if entry.get("expected") != frozen["expected"]:
            raise ValueError(
                f"published case id={case_id!r} expected mismatch: "
                f"published={entry.get('expected')!r}, "
                f"frozen={frozen['expected']!r}"
            )

    # STEP 5a — per-case side presence + essential fields.
    required_side_fields = frozenset(
        {"runtime", "validation", "business", "correct", "observable"}
    )
    for i, entry in enumerate(per_case):
        for side in ("ncp", "llm"):
            side_data = entry.get(side)
            if side_data is None:
                raise ValueError(
                    f"published per_case[{i}] (id={entry.get('id')!r}) "
                    f"missing {side!r} side; both sides required"
                )
            if not isinstance(side_data, dict):
                raise ValueError(
                    f"published per_case[{i}] (id={entry.get('id')!r}) "
                    f"side {side!r} is not a mapping"
                )
            missing_fields = required_side_fields - set(side_data.keys())
            if missing_fields:
                raise ValueError(
                    f"published per_case[{i}] (id={entry.get('id')!r}) "
                    f"side {side!r} missing essential fields: "
                    f"{sorted(missing_fields)!r}"
                )

    # STEP 5b — aggregate consistency with per-case sums.
    aggregates = comparison.get("aggregates", {})
    for side in ("ncp", "llm"):
        side_agg = aggregates.get(side)
        if side_agg is None:
            raise ValueError(f"published aggregates missing side: {side!r}")
        case_sum_calls = sum(entry[side].get("model_api_calls", 0) for entry in per_case)
        if side_agg.get("total_model_api_calls") != case_sum_calls:
            raise ValueError(
                f"published aggregates.{side}.total_model_api_calls mismatch: "
                f"aggregate={side_agg.get('total_model_api_calls')!r}, "
                f"per_case sum={case_sum_calls}"
            )
        case_sum_graph = sum(entry[side].get("graph_invocations", 0) for entry in per_case)
        if side_agg.get("total_graph_invocations") != case_sum_graph:
            raise ValueError(
                f"published aggregates.{side}.total_graph_invocations mismatch: "
                f"aggregate={side_agg.get('total_graph_invocations')!r}, "
                f"per_case sum={case_sum_graph}"
            )

    # STEP 6 — per-category coverage.
    per_category = comparison.get("per_category", {})
    for category in EXPECTED_CATEGORY_COUNTS:
        if category not in per_category:
            raise ValueError(f"published per_category missing category: {category!r}")
        bucket = per_category[category]
        for side in ("ncp", "llm"):
            if bucket.get(side) is None:
                raise ValueError(
                    f"published per_category[{category!r}] missing side: {side!r}"
                )


# --- Output writing (publication control) ---------------------------------


def write_comparison(comparison: dict[str, Any], args: argparse.Namespace) -> Path:
    """Write the comparison dict to disk per args; return the output path."""
    if args.publish:
        if args.skip_ncp or not args.run_llm:
            raise ValueError(
                "--publish requires both NCP and LLM sides to have run"
            )
        _validate_publication_ready(comparison)
        out_dir = EXAMPLE_DIR / "published"
        out_path = out_dir / "comparison.json"
        out_dir.mkdir(parents=True, exist_ok=True)
        # Overwrite is intentional for the canonical published artifact.
        out_path.write_text(
            json.dumps(comparison, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return out_path
    # Local: microsecond-precision timestamp + exclusive-create write.
    out_dir = EXAMPLE_DIR / "local"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    out_path = out_dir / f"comparison-{ts}.json"
    # "x" mode = exclusive-create: raises FileExistsError if a file at
    # this path already exists, preventing silent overwrite on (improbable)
    # microsecond collision.
    with out_path.open("x", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(comparison, indent=2, ensure_ascii=False) + "\n")
    return out_path


# --- CLI ------------------------------------------------------------------


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Run the lead-qualification-vs-llm comparison. Default: NCP only, "
            "write to local/comparison-<ts>.json."
        ),
    )
    p.add_argument(
        "--run-llm",
        action="store_true",
        help="Enable the LLM baseline side. Requires ANTHROPIC_API_KEY. "
        "Default: skip (CI-safe; no API key needed).",
    )
    p.add_argument(
        "--skip-ncp",
        action="store_true",
        help="Skip the NCP side (LLM-only run). Default: run NCP.",
    )
    p.add_argument(
        "--publish",
        action="store_true",
        help="Write to published/comparison.json instead of local/. "
        "Requires --run-llm AND NCP side (not --skip-ncp). "
        "Enforces full dataset identity + integrity before writing.",
    )
    p.add_argument(
        "--ncp-binary",
        default=None,
        help="Override NCP binary path. Else uses NCP_RUNTIME env var or SDK default.",
    )
    p.add_argument(
        "--fixture-set-hash",
        default=None,
        help="Expected fixture-set SHA-256. Must equal the committed "
        "fixtures-rev-01 hash; cannot bypass the committed-hash check.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)

    if args.run_llm and not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ERROR: --run-llm requires the ANTHROPIC_API_KEY environment variable.",
            file=sys.stderr,
        )
        return 2
    if args.publish and (args.skip_ncp or not args.run_llm):
        print(
            "ERROR: --publish requires both NCP (not --skip-ncp) and --run-llm.",
            file=sys.stderr,
        )
        return 2

    try:
        comparison = run_comparison(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    out_path = write_comparison(comparison, args)
    print(f"Comparison written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
