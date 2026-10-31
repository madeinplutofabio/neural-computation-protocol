# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Tests for baseline_llm.py.

Pins the strict response-extraction policy, measurement preservation,
API-error handling, sanitized diagnostics, and the §10 prompt-input
containment invariant. No real Anthropic API is called; all tests mock
`client.messages.create`.
"""

from __future__ import annotations

import hashlib
import inspect
import sys
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import anthropic
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import baseline_llm  # noqa: E402
from baseline_llm import (  # noqa: E402
    Pricing,
    render_prompt,
    run_llm,
)


# --- Helpers ---------------------------------------------------------------


PRICING = Pricing(
    model="claude-haiku-5-5",
    input_price_per_mtok=0.10,
    output_price_per_mtok=0.50,
    pricing_date="2026-10-31",
)

GOOD_INPUT: dict[str, Any] = {
    "company_url": "https://example.com",
    "target_icp": "b2b saas smb creators",
    "notes": None,
}


def _text_block(text: str) -> Mock:
    b = Mock()
    b.type = "text"
    b.text = text
    return b


def _usage(input_tokens: int | None = 100, output_tokens: int | None = 10) -> Mock:
    u = Mock()
    u.input_tokens = input_tokens
    u.output_tokens = output_tokens
    return u


def _response(
    *,
    content: list[Any] | None = None,
    stop_reason: str | None = "end_turn",
    usage: Mock | None = None,
    model: str | None = "claude-haiku-5-5-20261001",
) -> Mock:
    r = Mock()
    r.content = [] if content is None else content
    r.stop_reason = stop_reason
    r.usage = _usage() if usage is None else usage
    r.model = model
    return r


def _client_returning(response: Any) -> Mock:
    c = Mock()
    c.messages.create.return_value = response
    return c


def _client_raising(exc: Exception) -> Mock:
    c = Mock()
    c.messages.create.side_effect = exc
    return c


def _mk_api_error(cls: type, msg: str = "boom") -> Exception:
    """Instantiate an anthropic APIError subclass with varying signatures.

    SDK versions differ in constructor shapes; fall back progressively.
    """
    try:
        return cls(message=msg, request=Mock(), body=None)  # type: ignore[call-arg]
    except TypeError:
        pass
    try:
        return cls(msg)  # type: ignore[call-arg]
    except TypeError:
        pass
    exc = cls.__new__(cls)
    Exception.__init__(exc, msg)
    return exc


# --- §10 check 2: prompt-input containment --------------------------------


def test_render_prompt_signature_has_exactly_one_parameter():
    sig = inspect.signature(render_prompt)
    assert len(sig.parameters) == 1
    param = list(sig.parameters.values())[0]
    assert param.default is inspect.Parameter.empty
    assert param.kind in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.POSITIONAL_ONLY,
    )


def test_render_prompt_metadata_mutation_invariant():
    inp = {"company_url": "https://example.com", "target_icp": "x", "notes": None}
    f1_metadata = {
        "id": "a1",
        "expected": {"business_outcome": "qualified"},
        "category": "catalog_full_match",
        "notes_for_reviewer": "note A",
    }
    f2_metadata = {
        "id": "a2",
        "expected": {"validation": "invalid_input"},
        "category": "invalid_input",
        "notes_for_reviewer": "wildly different note B",
    }
    out1 = render_prompt(inp)
    out2 = render_prompt(dict(inp))
    assert out1 == out2
    fx1 = {"input": inp, **f1_metadata}
    fx2 = {"input": inp, **f2_metadata}
    assert render_prompt(fx1["input"]) == render_prompt(fx2["input"])


def test_render_prompt_substitutes_three_fields():
    out = render_prompt(
        {
            "company_url": "https://example.com",
            "target_icp": "b2b saas smb creators",
            "notes": None,
        }
    )
    assert "https://example.com" in out
    assert "b2b saas smb creators" in out
    assert "notes: null" in out


def test_render_prompt_notes_string_passed_through():
    out = render_prompt(
        {
            "company_url": "https://example.com",
            "target_icp": "x",
            "notes": "any note",
        }
    )
    assert "notes: any note" in out


def test_render_prompt_missing_required_key_propagates():
    with pytest.raises(KeyError):
        render_prompt({"company_url": "https://example.com"})


# --- Prompt template hash matches committed hash file ---------------------


def test_prompt_template_hash_matches_file():
    template_path = Path(baseline_llm.__file__).with_name("prompt-template.txt")
    hash_path = Path(baseline_llm.__file__).with_name("prompt-template.sha256")
    actual = hashlib.sha256(template_path.read_bytes()).hexdigest()
    expected = hash_path.read_text(encoding="utf-8").strip()
    assert actual == expected


# --- Valid-label extraction (strict single-block, exact-match) ------------


@pytest.mark.parametrize(
    "label", ["qualified", "not_qualified", "needs_enrichment", "invalid_input"]
)
def test_valid_label_extracted_correctly(label: str) -> None:
    resp = _response(content=[_text_block(label)])
    out = run_llm(GOOD_INPUT, client=_client_returning(resp), pricing=PRICING)
    impl = out["impl_result"]
    if label == "invalid_input":
        assert impl.runtime == "success"
        assert impl.validation == "invalid_input"
        assert impl.business is None
    else:
        assert impl.runtime == "success"
        assert impl.validation is None
        assert impl.business == label
    assert out["model_api_calls"] == 1
    assert out["graph_invocations"] == 0
    assert out["error"] is None


def test_trailing_whitespace_tolerated():
    resp = _response(content=[_text_block("qualified\n")])
    out = run_llm(GOOD_INPUT, client=_client_returning(resp), pricing=PRICING)
    assert out["impl_result"].business == "qualified"


def test_leading_whitespace_tolerated():
    resp = _response(content=[_text_block("  qualified  ")])
    out = run_llm(GOOD_INPUT, client=_client_returning(resp), pricing=PRICING)
    assert out["impl_result"].business == "qualified"


# --- Strict rejection: no splitlines[0], no extras, no case changes -------


@pytest.mark.parametrize(
    "text,label",
    [
        ("qualified\nReason: all three dims match", "multi-line with reasoning"),
        ("qualified Reason: all three", "label + inline reasoning"),
        ("Qualified", "capitalized"),
        ("QUALIFIED", "uppercase"),
        ('"qualified"', "quoted"),
        ("```qualified```", "code-fenced"),
        ("The answer is: qualified", "prefixed"),
        ("qualified.", "trailing punctuation"),
        ("mystery_label", "out-of-vocabulary"),
        ("", "empty string"),
        ("   ", "only whitespace"),
    ],
)
def test_malformed_labels_are_unresolved(text: str, label: str) -> None:
    resp = _response(content=[_text_block(text)])
    out = run_llm(GOOD_INPUT, client=_client_returning(resp), pricing=PRICING)
    impl = out["impl_result"]
    assert impl.runtime == "unresolved", f"case: {label}"
    assert impl.business is None, f"case: {label}"
    assert impl.validation is None, f"case: {label}"
    assert out["error"] is not None, f"case: {label}"
    assert out["error"].startswith("extraction_failure:")


# --- Structural response-shape rejection (strict: exactly one block) ------


def test_response_with_empty_content_is_unresolved():
    resp = _response(content=[])
    out = run_llm(GOOD_INPUT, client=_client_returning(resp), pricing=PRICING)
    assert out["impl_result"].runtime == "unresolved"
    assert "content_block_count_0" in out["error"]


def test_response_with_multiple_text_blocks_is_unresolved():
    # Two blocks, both text → still a structural violation per the
    # "exactly one content block" rule.
    resp = _response(content=[_text_block("qualified"), _text_block("extra")])
    out = run_llm(GOOD_INPUT, client=_client_returning(resp), pricing=PRICING)
    assert out["impl_result"].runtime == "unresolved"
    assert "content_block_count_2" in out["error"]


def test_response_with_single_non_text_block_is_unresolved():
    # One block of type "thinking" (no text block).
    thinking = Mock()
    thinking.type = "thinking"
    resp = _response(content=[thinking])
    out = run_llm(GOOD_INPUT, client=_client_returning(resp), pricing=PRICING)
    assert out["impl_result"].runtime == "unresolved"
    assert "content_block_not_text" in out["error"]


def test_response_with_text_plus_other_block_is_unresolved():
    # One text block + one non-text block (e.g. tool_use). Even though
    # a label is present in the text block, the response is structurally
    # non-conforming (not exactly one block).
    other = Mock()
    other.type = "tool_use"
    resp = _response(content=[_text_block("qualified"), other])
    out = run_llm(GOOD_INPUT, client=_client_returning(resp), pricing=PRICING)
    assert out["impl_result"].runtime == "unresolved"
    assert "content_block_count_2" in out["error"]


def test_response_content_not_list_is_unresolved():
    resp = _response()
    resp.content = "not a list"
    out = run_llm(GOOD_INPUT, client=_client_returning(resp), pricing=PRICING)
    assert out["impl_result"].runtime == "unresolved"
    assert "content_not_list" in out["error"]


# --- Policy-level non-classifications: refusal / max_tokens --------------


def test_refusal_is_runtime_failure_with_usage_preserved():
    resp = _response(
        content=[_text_block("I cannot help with that.")],
        stop_reason="refusal",
        usage=_usage(input_tokens=300, output_tokens=15),
    )
    out = run_llm(GOOD_INPUT, client=_client_returning(resp), pricing=PRICING)
    impl = out["impl_result"]
    assert impl.runtime == "failure"
    assert impl.validation is None
    assert impl.business is None
    assert out["tokens_in"] == 300
    assert out["tokens_out"] == 15
    expected_cost = (300 / 1e6) * 0.10 + (15 / 1e6) * 0.50
    assert out["model_cost_usd"] == pytest.approx(expected_cost)
    assert out["error"] == "stop_reason: refusal"


def test_max_tokens_is_unresolved_with_usage_preserved():
    resp = _response(
        content=[_text_block("qualif")],
        stop_reason="max_tokens",
        usage=_usage(input_tokens=250, output_tokens=256),
    )
    out = run_llm(GOOD_INPUT, client=_client_returning(resp), pricing=PRICING)
    impl = out["impl_result"]
    assert impl.runtime == "unresolved"
    assert out["tokens_in"] == 250
    assert out["tokens_out"] == 256
    assert out["model_cost_usd"] is not None
    assert out["error"] == "stop_reason: max_tokens"


# --- Usage preserved even on malformed-label extraction failure ----------


def test_usage_preserved_on_malformed_label():
    """Reviewer amendment #3: a classification failure that is NOT a
    policy non-classification (refusal / max_tokens) must still
    preserve response.usage. The HTTP request succeeded; tokens were
    consumed and must be reported."""
    resp = _response(
        content=[_text_block("Qualified")],  # case mismatch → extraction failure
        stop_reason="end_turn",
        usage=_usage(input_tokens=175, output_tokens=4),
    )
    out = run_llm(GOOD_INPUT, client=_client_returning(resp), pricing=PRICING)
    impl = out["impl_result"]
    assert impl.runtime == "unresolved"
    assert out["error"].startswith("extraction_failure:")
    assert out["tokens_in"] == 175
    assert out["tokens_out"] == 4
    expected_cost = (175 / 1e6) * 0.10 + (4 / 1e6) * 0.50
    assert out["model_cost_usd"] == pytest.approx(expected_cost)


# --- API errors (no response → tokens=None, cost=None) --------------------


@pytest.mark.parametrize(
    "exc_cls,label",
    [
        (anthropic.APITimeoutError, "timeout"),
        (anthropic.APIConnectionError, "connection"),
        (anthropic.RateLimitError, "rate_limit"),
        (anthropic.InternalServerError, "server_error"),
        (anthropic.BadRequestError, "bad_request"),
    ],
)
def test_api_error_subclasses_report_unresolved_with_no_usage(
    exc_cls: type, label: str
) -> None:
    exc = _mk_api_error(exc_cls)
    out = run_llm(GOOD_INPUT, client=_client_raising(exc), pricing=PRICING)
    impl = out["impl_result"]
    assert impl.runtime == "unresolved", f"case: {label}"
    assert out["model_api_calls"] == 1, f"case: {label}"
    assert out["graph_invocations"] == 0, f"case: {label}"
    assert out["tokens_in"] is None, f"case: {label}"
    assert out["tokens_out"] is None, f"case: {label}"
    assert out["model_cost_usd"] is None, f"case: {label}"
    assert out["llm_model_resolved"] is None, f"case: {label}"
    assert out["error"] is not None
    # Sanitized: must contain exception class name, no provider text.
    assert exc_cls.__name__ in out["error"]
    assert out["error"].startswith("api_error:")


# --- Diagnostic sanitization (reviewer amendment #1) ---------------------


def test_api_exception_message_does_not_leak_into_error():
    """A secret-shaped string inside the exception message must NOT
    appear in AdapterResult.error. Only the class name + optional
    status code should surface."""
    secret = "SECRET-ANTHROPIC-KEY-sk-abcdef12345"
    exc = _mk_api_error(anthropic.BadRequestError, msg=f"Bad request: {secret}")
    out = run_llm(GOOD_INPUT, client=_client_raising(exc), pricing=PRICING)
    assert out["impl_result"].runtime == "unresolved"
    assert out["error"] is not None
    assert secret not in out["error"]
    assert "BadRequestError" in out["error"]


def test_api_exception_response_body_does_not_leak_into_error():
    """A response excerpt attached to the exception message likewise
    must not surface in the diagnostic."""
    response_excerpt = "RESPONSE_BODY:{\"detail\":\"user prompt contains injection attempt\"}"
    exc = _mk_api_error(anthropic.APIStatusError, msg=response_excerpt)
    # APIStatusError may carry a numeric status_code; set one on the mock.
    try:
        exc.status_code = 500  # type: ignore[attr-defined]
    except AttributeError:
        pass
    out = run_llm(GOOD_INPUT, client=_client_raising(exc), pricing=PRICING)
    assert out["impl_result"].runtime == "unresolved"
    assert response_excerpt not in out["error"]
    assert "APIStatusError" in out["error"]


def test_extraction_failure_diagnostic_does_not_leak_response_text():
    secret = "sk-anthropic-00000000-DEADBEEF"
    resp = _response(content=[_text_block(f"{secret} rogue reasoning")])
    out = run_llm(GOOD_INPUT, client=_client_returning(resp), pricing=PRICING)
    assert out["impl_result"].runtime == "unresolved"
    assert secret not in (out["error"] or "")
    assert "extraction_failure" in out["error"]


def test_bounded_diag_truncates_long_detail():
    long_detail = "x" * 500
    out = baseline_llm._bounded_diag("prefix", long_detail)
    assert out.startswith("prefix: ")
    assert out.endswith("...")
    # Allow prefix + 200 chars + "..." suffix.
    assert len(out) <= len("prefix: ") + 200 + len("...")


# --- Usage, cost, resolved-model on normal path --------------------------


def test_usage_and_cost_computed_from_response():
    resp = _response(
        content=[_text_block("qualified")],
        usage=_usage(input_tokens=1000, output_tokens=20),
    )
    out = run_llm(GOOD_INPUT, client=_client_returning(resp), pricing=PRICING)
    assert out["tokens_in"] == 1000
    assert out["tokens_out"] == 20
    expected_cost = (1000 / 1e6) * 0.10 + (20 / 1e6) * 0.50
    assert out["model_cost_usd"] == pytest.approx(expected_cost)


def test_missing_usage_object_yields_nulls_not_zeros():
    resp = _response(content=[_text_block("qualified")])
    resp.usage = None
    out = run_llm(GOOD_INPUT, client=_client_returning(resp), pricing=PRICING)
    assert out["tokens_in"] is None
    assert out["tokens_out"] is None
    assert out["model_cost_usd"] is None


def test_partial_usage_yields_null_cost():
    usage = Mock()
    usage.input_tokens = 500
    usage.output_tokens = None
    resp = _response(content=[_text_block("qualified")], usage=usage)
    out = run_llm(GOOD_INPUT, client=_client_returning(resp), pricing=PRICING)
    assert out["tokens_in"] == 500
    assert out["tokens_out"] is None
    assert out["model_cost_usd"] is None


# --- Resolved-model capture ----------------------------------------------


def test_resolved_model_captured_from_response():
    resp = _response(
        content=[_text_block("qualified")],
        model="claude-haiku-5-5-20261001",
    )
    out = run_llm(GOOD_INPUT, client=_client_returning(resp), pricing=PRICING)
    assert out["llm_model_resolved"] == "claude-haiku-5-5-20261001"


def test_resolved_model_absent_yields_null():
    resp = _response(content=[_text_block("qualified")])
    resp.model = None
    out = run_llm(GOOD_INPUT, client=_client_returning(resp), pricing=PRICING)
    assert out["llm_model_resolved"] is None


# --- model_api_calls accounting ------------------------------------------


def test_model_api_calls_is_one_on_successful_classification():
    resp = _response(content=[_text_block("qualified")])
    out = run_llm(GOOD_INPUT, client=_client_returning(resp), pricing=PRICING)
    assert out["model_api_calls"] == 1


def test_model_api_calls_is_one_on_api_error():
    exc = _mk_api_error(anthropic.APITimeoutError)
    out = run_llm(GOOD_INPUT, client=_client_raising(exc), pricing=PRICING)
    assert out["model_api_calls"] == 1


# --- Programmer errors propagate ------------------------------------------


def test_programmer_error_in_extraction_propagates(monkeypatch):
    def boom(response: Any) -> tuple[str | None, str | None]:
        raise AttributeError("simulated programmer bug")

    monkeypatch.setattr(baseline_llm, "_extract_label", boom)
    resp = _response(content=[_text_block("qualified")])
    with pytest.raises(AttributeError, match="simulated programmer bug"):
        run_llm(GOOD_INPUT, client=_client_returning(resp), pricing=PRICING)


def test_missing_input_key_bubbles_up_as_programmer_error():
    bad_input = {"company_url": "https://example.com"}  # target_icp missing
    with pytest.raises(KeyError):
        run_llm(bad_input, client=Mock(), pricing=PRICING)
