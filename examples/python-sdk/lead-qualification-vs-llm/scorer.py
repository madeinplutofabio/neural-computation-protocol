# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Correctness scorer for the lead-qualification-vs-llm example.

Pure evaluation logic implementing CONTRACT.md §8 verbatim. Consumes
normalized ImplResult objects produced by the execution adapters
(ncp_version.py / baseline_llm.py); emits per-case scores and §8.2
aggregates.

Scope boundary (CONTRACT.md §9.1/§9.2 architectural rule):
  * This module imports stdlib only.
  * It does NOT import ncp_runtime, anthropic, requests, or any other
    provider SDK.
  * It does NOT parse raw NCP terminal objects or Anthropic responses.
    Per-provider extraction belongs in the adapters; the scorer sees
    only the normalized ImplResult shape (§6.1).
  * It does NOT fabricate measurements. Tokens, costs, call counts,
    and latency are the adapters' and comparison orchestrator's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypedDict

Runtime = Literal["success", "failure", "unresolved"]
Validation = Literal["invalid_input"]
Business = Literal["qualified", "not_qualified", "needs_enrichment"]

_VALID_RUNTIMES: frozenset[str] = frozenset({"success", "failure", "unresolved"})
_VALID_VALIDATIONS: frozenset[str] = frozenset({"invalid_input"})
_VALID_BUSINESSES: frozenset[str] = frozenset(
    {"qualified", "not_qualified", "needs_enrichment"}
)


@dataclass(frozen=True, slots=True)
class ImplResult:
    """Normalized classification outcome from either implementation.

    See CONTRACT.md §6.1 for the three axes and §6.2 for the invariant
    that validation="invalid_input" MUST be set only for an observable,
    input-specific rejection (never for infrastructure failures).

    __post_init__ enforces the following invariants at construction
    time, so corrupt states from an adapter never reach the scorer:
      1. Vocabulary: runtime / validation / business must be in the
         frozen enumerations above (Python Literal is a type-checker
         hint, not a runtime constraint).
      2. Mutual exclusivity: at most one of validation and business
         is non-null (§6.1).
      3. Unresolved cannot carry an observable outcome: runtime=
         "unresolved" with validation or business non-null is a
         contradiction (§6.1 — unresolved means the system never
         produced a decision from the input).
      4. Business requires success: business non-null is only valid
         with runtime="success" (§6.1 — a business classification
         is the output of a successful completed execution).
    """

    runtime: Runtime
    validation: Validation | None
    business: Business | None

    def __post_init__(self) -> None:
        # 1. Vocabulary enforcement.
        if self.runtime not in _VALID_RUNTIMES:
            raise ValueError(
                f"Invalid runtime value {self.runtime!r}; "
                f"expected one of {sorted(_VALID_RUNTIMES)}"
            )
        if self.validation is not None and self.validation not in _VALID_VALIDATIONS:
            raise ValueError(
                f"Invalid validation value {self.validation!r}; "
                f"expected None or one of {sorted(_VALID_VALIDATIONS)}"
            )
        if self.business is not None and self.business not in _VALID_BUSINESSES:
            raise ValueError(
                f"Invalid business value {self.business!r}; "
                f"expected None or one of {sorted(_VALID_BUSINESSES)}"
            )

        # 2. §6.1 mutual exclusivity.
        if self.validation is not None and self.business is not None:
            raise ValueError(
                "ImplResult violates CONTRACT.md §6.1: "
                f"validation={self.validation!r} AND business={self.business!r} "
                "cannot both be non-null"
            )

        # 3. §6.1 unresolved cannot carry observable outcome.
        if self.runtime == "unresolved" and (
            self.validation is not None or self.business is not None
        ):
            raise ValueError(
                "ImplResult violates CONTRACT.md §6.1: "
                f"runtime='unresolved' cannot carry validation="
                f"{self.validation!r} or business={self.business!r}; "
                "unresolved execution produces no observable outcome"
            )

        # 4. §6.1 business requires runtime='success'.
        if self.business is not None and self.runtime != "success":
            raise ValueError(
                "ImplResult violates CONTRACT.md §6.1: "
                f"business={self.business!r} requires runtime='success', "
                f"got runtime={self.runtime!r}"
            )


class ScoredCase(TypedDict):
    """Per-case scoring result.

    `observable` is a derived flag used by aggregate() to compute the
    completed-only denominator (§8.2). An outcome is observable iff it
    is either a successful business classification OR an explicit
    input-validation rejection. Infrastructure failures (`unresolved`,
    or `failure` with `validation` null) are NOT observable.
    """

    id: str
    category: str
    correct: bool
    runtime: Runtime
    validation: Validation | None
    business: Business | None
    observable: bool


class AdapterResult(TypedDict):
    """Carrier for one execution-adapter invocation's full result.

    Produced by ncp_version.run_ncp() / baseline_llm.run_llm().
    Consumed by compare.py: `impl_result` is routed to the scorer;
    the remaining fields are serialized into comparison.json's per-case
    entry (§9.4).

    Measurement-field conventions:
      * NCP side: model_api_calls=0, graph_invocations in {0, 1},
        tokens_in=0, tokens_out=0, model_cost_usd=0.0 (deterministic
        absence of LLM involvement, known with certainty — not null).
      * LLM side: graph_invocations=0, model_api_calls in {0, 1}
        (count of attempted API requests), tokens_in/tokens_out from
        provider usage object or `None` when provider didn't report
        (§8.5 null-propagation rule), model_cost_usd computed from
        tokens or `None`.

    `llm_model_resolved` is the provider's actual returned model
    identifier (from `response.model`) when available, preserved
    alongside the requested `claude-haiku-5-5` alias so published
    results remain traceable if the alias ever resolves to a different
    snapshot. `None` on the NCP side (no analogous notion) and when
    the provider didn't return a usable `model` field.

    `error` is an optional diagnostic string for debugging. Bounded by
    the adapter; carries only exception class names, enum stop_reason
    values, numeric status codes, and short structural codes. Never
    carries raw response content, API keys, exception messages, or
    arbitrary provider-side text. Not consumed by the scorer; surfaced
    in comparison.json for reproducibility.
    """

    impl_result: ImplResult
    latency_ms: float
    model_api_calls: int
    graph_invocations: int
    tokens_in: int | None
    tokens_out: int | None
    model_cost_usd: float | None
    llm_model_resolved: str | None
    error: str | None


def has_observable_outcome(result: ImplResult) -> bool:
    """Return True iff the result is a decision reached by examining input.

    Per §8.2: observable = (completed business classification) OR
    (explicit input-validation rejection). NCP's Failure(INVALID_INPUT)
    is observable despite runtime=="failure"; an API 500 or missing
    binary is NOT observable despite potentially also being "failure".
    """
    if result.business is not None and result.runtime == "success":
        return True
    if result.validation == "invalid_input":
        return True
    return False


def score_case(fixture: dict[str, Any], impl_result: ImplResult) -> ScoredCase:
    """Score one fixture against one implementation result per §8.1.

    Validation branch: the fixture's `expected` has shape
    `{"validation": "invalid_input"}`. Correct iff the implementation's
    validation axis matches, regardless of runtime axis. Safety rests
    on the adapter emitting `validation="invalid_input"` ONLY for
    observable, input-specific rejections (§6.2; enforced in the
    adapter + ImplResult's __post_init__, not here).

    Business branch: the fixture's `expected` has shape
    `{"business_outcome": <label>}`. Correct iff `runtime == "success"`
    AND the business label matches.
    """
    expected = fixture["expected"]
    correct: bool
    if "validation" in expected:
        correct = impl_result.validation == expected["validation"]
    else:
        correct = (
            impl_result.runtime == "success"
            and impl_result.business == expected["business_outcome"]
        )
    return ScoredCase(
        id=fixture["id"],
        category=fixture["category"],
        correct=correct,
        runtime=impl_result.runtime,
        validation=impl_result.validation,
        business=impl_result.business,
        observable=has_observable_outcome(impl_result),
    )


class Aggregates(TypedDict):
    """Aggregate scoring statistics over a set of scored cases.

    Follows §8.2 verbatim:
      * total: denominator for all-attempted accuracy and completion rate.
      * accuracy_all_attempted: correct / total. Infrastructure failures
        count as incorrect; this is the honest baseline.
      * completed: observable-outcome count. Used as denominator for
        completed-only accuracy.
      * accuracy_completed_only: correct_among_observable / completed.
        null when completed == 0 (defined by §8.2 as the "no completed
        cases" result).
      * completion_rate: completed / total.
    """

    total: int
    correct_all_attempted: int
    accuracy_all_attempted: float
    completed: int
    correct_completed: int
    accuracy_completed_only: float | None
    completion_rate: float


def aggregate(scored: list[ScoredCase]) -> Aggregates:
    """Compute §8.2 headline metrics over the scored cases."""
    total = len(scored)
    if total == 0:
        return Aggregates(
            total=0,
            correct_all_attempted=0,
            accuracy_all_attempted=0.0,
            completed=0,
            correct_completed=0,
            accuracy_completed_only=None,
            completion_rate=0.0,
        )
    correct_all_attempted = sum(1 for s in scored if s["correct"])
    completed = sum(1 for s in scored if s["observable"])
    correct_completed = sum(1 for s in scored if s["observable"] and s["correct"])
    accuracy_completed_only: float | None = (
        None if completed == 0 else correct_completed / completed
    )
    return Aggregates(
        total=total,
        correct_all_attempted=correct_all_attempted,
        accuracy_all_attempted=correct_all_attempted / total,
        completed=completed,
        correct_completed=correct_completed,
        accuracy_completed_only=accuracy_completed_only,
        completion_rate=completed / total,
    )


def aggregate_by_category(scored: list[ScoredCase]) -> dict[str, Aggregates]:
    """§8.3: stratified per-category aggregates, sorted by category name."""
    buckets: dict[str, list[ScoredCase]] = {}
    for s in scored:
        buckets.setdefault(s["category"], []).append(s)
    return {cat: aggregate(cases) for cat, cases in sorted(buckets.items())}
