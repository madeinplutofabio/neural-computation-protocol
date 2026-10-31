# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""LLM-side execution adapter for the lead-qualification-vs-llm example.

Takes a {company_url, target_icp, notes} input dict and classifies the
lead using claude-haiku-5-5 via the anthropic Python SDK. Returns an
AdapterResult (defined in scorer.py) carrying the normalized ImplResult
plus measurement fields.

See CONTRACT.md:
  * §6.1 — runtime axes and their LLM sources
  * §9.1 — LLM baseline configuration (model, thinking, effort, retries, prompt)
  * §9.3 — pricing anchor
  * §9.4 — per-case measurement fields (incl. model_api_calls semantic)
  * §10 — fairness invariants (prompt-input containment)

Response-extraction policy (strict):
  * Response must contain exactly one content block (len == 1).
  * That block must be of type "text" with a string `.text` attribute.
  * The text's stripped content must be exactly one of the four
    permitted labels (§6 vocabulary).
  * Any deviation becomes runtime="unresolved" with a bounded diagnostic.
  * No raw response content leaks into AdapterResult.error.

Measurement preservation (§8.5):
  * Token counts and cost come from response.usage when the HTTP
    request succeeded, EVEN IF label extraction later fails or
    stop_reason is "refusal" / "max_tokens". A failed classification
    does not imply zero API usage.
  * Only when there is no response at all (network/HTTP error) are
    tokens and cost reported as None.

Diagnostic sanitization policy:
  * Error strings are bounded to 200 characters.
  * API-error diagnostics carry ONLY the exception class name and an
    optional numeric HTTP status code — NEVER the exception message,
    response body, request body, prompt text, or API keys.
  * Extraction-failure diagnostics carry short structural codes only.
  * stop_reason diagnostics carry only the enum string.

Exception-handling policy:
  * Expected operational failures (anthropic.APIError subclasses) are
    caught and reported as runtime="unresolved".
  * Programmer-level errors (KeyError for a missing input key, bugs
    in extraction) propagate visibly.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from string import Template
from typing import Any

import anthropic
from anthropic import Anthropic

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scorer import AdapterResult, ImplResult  # noqa: E402


# --- Frozen configuration --------------------------------------------------

MODEL = "claude-haiku-5-5"
MAX_TOKENS = 256

_VALID_LABELS: frozenset[str] = frozenset(
    {"qualified", "not_qualified", "needs_enrichment", "invalid_input"}
)
_VALID_BUSINESS_LABELS: frozenset[str] = frozenset(
    {"qualified", "not_qualified", "needs_enrichment"}
)

# Load the frozen prompt template once at module import, relative to
# this file (robust against caller's cwd).
_TEMPLATE_PATH = Path(__file__).with_name("prompt-template.txt")
_TEMPLATE = Template(_TEMPLATE_PATH.read_text(encoding="utf-8"))

# Bounded-diagnostic max length (characters).
_DIAG_MAX = 200


# --- Pricing anchor type ---------------------------------------------------


class Pricing:
    """Loaded pricing anchor; used only to compute per-case cost."""

    __slots__ = (
        "model",
        "input_price_per_mtok",
        "output_price_per_mtok",
        "pricing_date",
    )

    def __init__(
        self,
        model: str,
        input_price_per_mtok: float,
        output_price_per_mtok: float,
        pricing_date: str,
    ) -> None:
        self.model = model
        self.input_price_per_mtok = input_price_per_mtok
        self.output_price_per_mtok = output_price_per_mtok
        self.pricing_date = pricing_date


# --- Prompt rendering (§10 check 2: one-param, no fixture metadata) -------


def render_prompt(input_dict: dict[str, Any]) -> str:
    """Render the frozen prompt template with the lead's three input fields.

    Signature deliberately exposes exactly one parameter (§10 check 2).
    Mapping: {company_url, target_icp, notes} → ${COMPANY_URL},
    ${TARGET_ICP}, ${NOTES}. notes=None is rendered as the literal
    four-character string "null".

    Missing required keys (`company_url`, `target_icp`) are a programmer
    error and propagate as KeyError from the subscript access.
    """
    notes_value = input_dict.get("notes")
    rendered_notes: str = "null" if notes_value is None else str(notes_value)
    return _TEMPLATE.substitute(
        COMPANY_URL=str(input_dict["company_url"]),
        TARGET_ICP=str(input_dict["target_icp"]),
        NOTES=rendered_notes,
    )


# --- Diagnostic helpers (sanitized) ---------------------------------------


def _bounded_diag(prefix: str, detail: str) -> str:
    """Return `prefix: detail` with detail truncated to _DIAG_MAX chars.

    `detail` must be a caller-chosen safe string (exception class name,
    stop_reason enum value, short structural failure code, numeric
    status code). This helper does NOT and MUST NOT be called with raw
    response content, prompt text, API keys, exception messages, or
    arbitrary user-provided data.
    """
    if len(detail) > _DIAG_MAX:
        detail = detail[:_DIAG_MAX] + "..."
    return f"{prefix}: {detail}"


def _api_error_detail(exc: anthropic.APIError) -> str:
    """Build a sanitized detail string for an API exception.

    Includes ONLY the exception class name and (when the exception
    carries a numeric status_code attribute) the HTTP status code.
    Explicitly excludes exc.message, str(exc), and any response body
    to prevent accidental publication of provider-side text that may
    contain request/response excerpts or other sensitive data.
    """
    detail = type(exc).__name__
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        detail = f"{detail} status={status}"
    return detail


# --- Response extraction (strict: exactly one text content block) ---------


def _extract_single_text_block(response: Any) -> tuple[str | None, str | None]:
    """Return (text, error_code).

    (<str>, None): response.content is a list of length 1, and that one
      block has type "text" with a string `.text` attribute. <str> is
      the raw `.text` content (not yet stripped or label-validated).
    (None, <code>): structural failure; <code> is a short, non-sensitive
      token suitable for the diagnostic string.

    Policy: enforces exactly one content block (len == 1), rather than
    "exactly one text block among possibly many". A response containing
    a text block plus any other block (thinking, tool_use, etc.) is
    rejected as a structural deviation.
    """
    content = getattr(response, "content", None)
    if content is None:
        return None, "no_content"
    if not isinstance(content, list):
        return None, "content_not_list"
    if len(content) != 1:
        return None, f"content_block_count_{len(content)}"
    block = content[0]
    if getattr(block, "type", None) != "text":
        return None, "content_block_not_text"
    text = getattr(block, "text", None)
    if not isinstance(text, str):
        return None, "text_not_string"
    return text, None


def _extract_label(response: Any) -> tuple[str | None, str | None]:
    """Return (label, error_code).

    (<label>, None): the single text block's stripped content is
      exactly one of the four permitted labels.
    (None, <code>): structural or vocabulary failure.
    """
    text, err = _extract_single_text_block(response)
    if err is not None:
        return None, err
    assert text is not None
    stripped = text.strip()
    if stripped not in _VALID_LABELS:
        return None, "label_not_in_vocabulary"
    return stripped, None


# --- Usage, cost, and resolved-model extraction ---------------------------


def _usage_and_cost(
    response: Any, pricing: Pricing
) -> tuple[int | None, int | None, float | None]:
    """Pull token counts from response.usage; compute per-case cost."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return None, None, None
    tokens_in = getattr(usage, "input_tokens", None)
    tokens_out = getattr(usage, "output_tokens", None)
    tokens_in_valid = isinstance(tokens_in, int) and not isinstance(tokens_in, bool)
    tokens_out_valid = isinstance(tokens_out, int) and not isinstance(tokens_out, bool)
    if not tokens_in_valid or not tokens_out_valid:
        return (
            tokens_in if tokens_in_valid else None,
            tokens_out if tokens_out_valid else None,
            None,
        )
    cost = (tokens_in / 1_000_000.0) * pricing.input_price_per_mtok + (
        tokens_out / 1_000_000.0
    ) * pricing.output_price_per_mtok
    return tokens_in, tokens_out, cost


def _resolved_model(response: Any) -> str | None:
    """Return response.model if present as a string, else None."""
    m = getattr(response, "model", None)
    return m if isinstance(m, str) else None


# --- Main entry point -----------------------------------------------------


def run_llm(
    input_dict: dict[str, Any],
    *,
    client: Anthropic,
    pricing: Pricing,
) -> AdapterResult:
    """Classify one lead using claude-haiku-5-5; return AdapterResult."""
    start = time.perf_counter()
    model_api_calls = 0

    # Prompt rendering (pre-attempt). KeyError here is a programmer bug.
    prompt = render_prompt(input_dict)

    try:
        model_api_calls = 1  # about to attempt the API call
        response = client.messages.create(
            model=MODEL,
            thinking={"type": "disabled"},
            output_config={"effort": "low"},
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as exc:
        # Sanitized diagnostic: class name + optional status code only.
        # Explicitly does NOT include str(exc) or any provider-side
        # message content, which could carry request/response excerpts.
        return AdapterResult(
            impl_result=ImplResult(runtime="unresolved", validation=None, business=None),
            latency_ms=(time.perf_counter() - start) * 1000.0,
            model_api_calls=model_api_calls,
            graph_invocations=0,
            tokens_in=None,
            tokens_out=None,
            model_cost_usd=None,
            llm_model_resolved=None,
            error=_bounded_diag("api_error", _api_error_detail(exc)),
        )

    latency_ms = (time.perf_counter() - start) * 1000.0

    # Preserve measurements FIRST — the HTTP layer succeeded, so usage
    # (when the provider returned it) is real spend regardless of
    # whether we can extract a valid label below.
    tokens_in, tokens_out, cost = _usage_and_cost(response, pricing)
    resolved_model = _resolved_model(response)

    stop_reason = getattr(response, "stop_reason", None)

    if stop_reason == "refusal":
        return AdapterResult(
            impl_result=ImplResult(runtime="failure", validation=None, business=None),
            latency_ms=latency_ms,
            model_api_calls=model_api_calls,
            graph_invocations=0,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model_cost_usd=cost,
            llm_model_resolved=resolved_model,
            error=_bounded_diag("stop_reason", "refusal"),
        )

    if stop_reason == "max_tokens":
        return AdapterResult(
            impl_result=ImplResult(runtime="unresolved", validation=None, business=None),
            latency_ms=latency_ms,
            model_api_calls=model_api_calls,
            graph_invocations=0,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model_cost_usd=cost,
            llm_model_resolved=resolved_model,
            error=_bounded_diag("stop_reason", "max_tokens"),
        )

    label, err_code = _extract_label(response)
    if label is None:
        assert err_code is not None
        return AdapterResult(
            impl_result=ImplResult(runtime="unresolved", validation=None, business=None),
            latency_ms=latency_ms,
            model_api_calls=model_api_calls,
            graph_invocations=0,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model_cost_usd=cost,
            llm_model_resolved=resolved_model,
            error=_bounded_diag("extraction_failure", err_code),
        )

    # Map the valid label to ImplResult axes.
    if label == "invalid_input":
        impl = ImplResult(runtime="success", validation="invalid_input", business=None)
    else:
        assert label in _VALID_BUSINESS_LABELS
        impl = ImplResult(
            runtime="success",
            validation=None,
            business=label,  # type: ignore[arg-type]
        )

    return AdapterResult(
        impl_result=impl,
        latency_ms=latency_ms,
        model_api_calls=model_api_calls,
        graph_invocations=0,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        model_cost_usd=cost,
        llm_model_resolved=resolved_model,
        error=None,
    )
