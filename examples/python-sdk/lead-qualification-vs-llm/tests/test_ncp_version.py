# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Tests for ncp_version.py.

Covers the five mapping categories flagged in the File 4 scope review:
  1. Success + well-formed qualifier terminal → business label
  2. Failure with INVALID_INPUT error_class → validation="invalid_input"
  3. Failure with any other error_class → runtime="failure"
  4. Success with malformed qualifier terminal → runtime="unresolved"
  5. SDK exceptions from NCP.run() → graph_invocations=1 (per §9.4)

Also pins:
  * Trace cleanup runs on success AND when processing raises.
  * Programmer errors during processing propagate visibly.
  * Binary-resolution precedence (explicit kwarg / env var / neither).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest
from ncp_runtime import (
    NCPBinaryNotFoundError,
    NCPRuntimeVersionError,
    NCPSubprocessError,
    NCPTimeoutError,
    NCPTraceSchemaError,
    Result,
    Terminal,
    TerminalError,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ncp_version  # noqa: E402
from ncp_version import run_ncp  # noqa: E402


# --- Helpers ----------------------------------------------------------------


def _terminal(
    node_id: str,
    result_type: str,
    output: Any = None,
    error: TerminalError | None = None,
    step: int = 0,
    brick_id: str = "test.brick",
) -> Terminal:
    return Terminal(
        node_id=node_id,
        brick_id=brick_id,
        step=step,
        result_type=result_type,
        output=output,
        error=error,
    )


def _result(terminals: tuple[Terminal, ...], result_type: str, tmp_path: Path) -> Result:
    trace = tmp_path / f"trace-{result_type}.jsonl"
    trace.touch()
    return Result(terminals=terminals, result_type=result_type, trace_path=trace)


def _lookup_success() -> Terminal:
    return _terminal(
        "lookup",
        "Success",
        output={
            "company_url_normalized": "example.com",
            "profile_found": True,
            "company_name": "Example SaaS",
            "industry": "B2B SaaS",
            "segment": "SMB",
            "audience": "creators",
            "target_icp": "b2b saas smb creators",
            "notes": None,
        },
    )


def _qualifier_success(next_action: str) -> Terminal:
    return _terminal(
        "qualify",
        "Success",
        output={
            "company_name": "Example SaaS",
            "industry": "B2B SaaS",
            "icp_match": next_action == "qualified",
            "score": 1.0 if next_action == "qualified" else 0.0,
            "reasons": ["mock reason"],
            "next_action": next_action,
        },
    )


FIXTURE_INPUT: dict[str, Any] = {
    "company_url": "https://example.com",
    "target_icp": "b2b saas smb creators",
    "notes": None,
}


def _run_with_fake(result: Result, tmp_path: Path, **run_kwargs: Any) -> dict[str, Any]:
    """Patch NCP class to return a provided fake Result; call run_ncp."""
    with patch("ncp_version.NCP") as mock_cls:
        mock_cls.return_value = Mock()
        mock_cls.return_value.run.return_value = result
        return dict(
            run_ncp(
                FIXTURE_INPUT,
                repo_root=tmp_path,
                binary_override=run_kwargs.pop("binary_override", "/fake/ncp"),
            )
        )


# --- Category 1: Success + well-formed qualifier terminal -------------------


@pytest.mark.parametrize(
    "next_action", ["qualified", "not_qualified", "needs_enrichment"]
)
def test_success_business_label_extraction(tmp_path: Path, next_action: str) -> None:
    fake = _result(
        (_lookup_success(), _qualifier_success(next_action)),
        "Success",
        tmp_path,
    )
    out = _run_with_fake(fake, tmp_path)
    impl = out["impl_result"]
    assert impl.runtime == "success"
    assert impl.validation is None
    assert impl.business == next_action
    assert out["graph_invocations"] == 1
    assert out["model_api_calls"] == 0
    assert out["tokens_in"] == 0
    assert out["tokens_out"] == 0
    assert out["model_cost_usd"] == 0.0
    assert out["error"] is None


# --- Category 2: Failure with INVALID_INPUT ---------------------------------


def test_failure_invalid_input(tmp_path: Path) -> None:
    err_term = _terminal(
        "lookup",
        "Failure",
        error=TerminalError(
            error_class="INVALID_INPUT",
            message="company_url: non-ASCII character",
        ),
    )
    out = _run_with_fake(_result((err_term,), "Failure", tmp_path), tmp_path)
    impl = out["impl_result"]
    assert impl.runtime == "failure"
    assert impl.validation == "invalid_input"
    assert impl.business is None
    assert out["graph_invocations"] == 1


# --- Category 3: Failure with some other error_class ------------------------


def test_failure_other_error_class(tmp_path: Path) -> None:
    err_term = _terminal(
        "qualify",
        "Failure",
        error=TerminalError(
            error_class="BRICK_EXECUTION_ERROR",
            message="runtime error",
        ),
    )
    out = _run_with_fake(
        _result((_lookup_success(), err_term), "Failure", tmp_path), tmp_path
    )
    impl = out["impl_result"]
    assert impl.runtime == "failure"
    assert impl.validation is None
    assert impl.business is None
    assert out["graph_invocations"] == 1


# --- Category 4: Success + malformed qualifier terminal --------------------


@pytest.mark.parametrize(
    "malformed_qualifier,label",
    [
        (None, "missing qualifier"),
        (_terminal("qualify", "Success", output="not a dict"), "non-dict output"),
        (
            _terminal(
                "qualify",
                "Success",
                output={"company_name": "x", "industry": "y"},
            ),
            "missing next_action",
        ),
        (
            _terminal(
                "qualify",
                "Success",
                output={"next_action": "mystery_label"},
            ),
            "invalid next_action value",
        ),
        (
            _terminal("qualify", "LowConfidence", output={"next_action": "qualified"}),
            "non-success qualifier terminal",
        ),
    ],
)
def test_success_with_malformed_qualifier_is_unresolved(
    tmp_path: Path, malformed_qualifier: Terminal | None, label: str
) -> None:
    terminals: tuple[Terminal, ...] = (
        (_lookup_success(),)
        if malformed_qualifier is None
        else (_lookup_success(), malformed_qualifier)
    )
    fake = _result(terminals, "Success", tmp_path)
    out = _run_with_fake(fake, tmp_path)
    impl = out["impl_result"]
    assert impl.runtime == "unresolved", f"case: {label}"
    assert impl.validation is None
    assert impl.business is None
    assert out["graph_invocations"] == 1
    assert out["error"] is not None
    assert "qualifier terminal" in out["error"]


# --- Category 4b: LowConfidence rollup (defensive) --------------------------


def test_lowconfidence_rollup_is_unresolved(tmp_path: Path) -> None:
    fake = _result(
        (_lookup_success(), _qualifier_success("qualified")),
        "LowConfidence",
        tmp_path,
    )
    out = _run_with_fake(fake, tmp_path)
    impl = out["impl_result"]
    assert impl.runtime == "unresolved"
    assert out["graph_invocations"] == 1
    assert out["error"] is not None
    assert "LowConfidence" in out["error"]


# --- Category 5: ALL SDK exceptions from NCP.run() → graph_invocations=1 ---
# Per §9.4 clarification: graph_invocations counts calls submitted to
# NCP.run(). An NCPTimeoutError may fire during the pre-flight version
# probe OR during graph execution; the adapter cannot distinguish, so
# every SDK exception emanating from NCP.run() counts as 1.


def _make_subprocess_error() -> NCPSubprocessError:
    return NCPSubprocessError(
        category="nonzero_exit",
        exit_code=1,
        binary_path=Path("/fake/ncp"),
        stderr=b"some stderr",
    )


@pytest.mark.parametrize(
    "exc_factory,label",
    [
        (lambda: NCPBinaryNotFoundError("binary not found"), "binary_not_found"),
        (lambda: NCPRuntimeVersionError("version mismatch"), "version_mismatch"),
        (lambda: NCPTimeoutError("timeout exceeded"), "timeout"),
        (_make_subprocess_error, "subprocess"),
        (lambda: NCPTraceSchemaError("bad trace"), "trace_schema"),
    ],
)
def test_sdk_exceptions_from_run_report_one_invocation(
    tmp_path: Path, exc_factory: Any, label: str
) -> None:
    with patch("ncp_version.NCP") as mock_cls:
        mock_cls.return_value = Mock()
        mock_cls.return_value.run.side_effect = exc_factory()
        out = run_ncp(FIXTURE_INPUT, repo_root=tmp_path, binary_override="/fake/ncp")
    impl = out["impl_result"]
    assert impl.runtime == "unresolved", f"case: {label}"
    assert out["graph_invocations"] == 1, f"case: {label}"
    assert out["error"] is not None


# --- Cleanup invariants ----------------------------------------------------


def test_result_cleanup_called_on_success(tmp_path: Path) -> None:
    # Result uses __slots__ so instance-level patching isn't possible;
    # patch at the class level, which routes every instance's cleanup()
    # call through the mock.
    fake = _result(
        (_lookup_success(), _qualifier_success("qualified")),
        "Success",
        tmp_path,
    )
    with patch("ncp_runtime.types.Result.cleanup", autospec=True) as mock_cleanup:
        with patch("ncp_version.NCP") as mock_cls:
            mock_cls.return_value = Mock()
            mock_cls.return_value.run.return_value = fake
            run_ncp(FIXTURE_INPUT, repo_root=tmp_path, binary_override="/fake/ncp")
        mock_cleanup.assert_called_once()


def test_result_cleanup_called_even_when_processing_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Inject a programmer-level error inside _process_result's extraction
    # helper; cleanup MUST still run via the try/finally.
    fake = _result(
        (_lookup_success(), _qualifier_success("qualified")),
        "Success",
        tmp_path,
    )

    def boom(_result: Result) -> str | None:
        raise RuntimeError("simulated programmer bug")

    monkeypatch.setattr(ncp_version, "_extract_business_label", boom)

    with patch("ncp_runtime.types.Result.cleanup", autospec=True) as mock_cleanup:
        with patch("ncp_version.NCP") as mock_cls:
            mock_cls.return_value = Mock()
            mock_cls.return_value.run.return_value = fake
            with pytest.raises(RuntimeError, match="simulated programmer bug"):
                run_ncp(FIXTURE_INPUT, repo_root=tmp_path, binary_override="/fake/ncp")
        mock_cleanup.assert_called_once()


def test_programming_error_in_processing_bubbles_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Independent assertion: programmer bugs are NOT swallowed as
    # runtime="unresolved". A broken adapter must fail visibly rather
    # than quietly produce 50 unresolved cases.
    fake = _result(
        (_lookup_success(), _qualifier_success("qualified")),
        "Success",
        tmp_path,
    )

    def boom(_result: Result) -> str | None:
        raise AttributeError("something internal broke")

    monkeypatch.setattr(ncp_version, "_extract_business_label", boom)

    with patch("ncp_version.NCP") as mock_cls:
        mock_cls.return_value = Mock()
        mock_cls.return_value.run.return_value = fake
        with pytest.raises(AttributeError, match="something internal broke"):
            run_ncp(FIXTURE_INPUT, repo_root=tmp_path, binary_override="/fake/ncp")


# --- Binary resolution plumbing --------------------------------------------


def test_binary_override_passed_through(tmp_path: Path) -> None:
    fake = _result(
        (_lookup_success(), _qualifier_success("qualified")),
        "Success",
        tmp_path,
    )
    with patch("ncp_version.NCP") as mock_cls:
        mock_cls.return_value = Mock()
        mock_cls.return_value.run.return_value = fake
        run_ncp(FIXTURE_INPUT, repo_root=tmp_path, binary_override="/explicit/path")
        mock_cls.assert_called_once_with(binary="/explicit/path")


def test_no_binary_override_lets_sdk_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NCP_RUNTIME", raising=False)
    fake = _result(
        (_lookup_success(), _qualifier_success("qualified")),
        "Success",
        tmp_path,
    )
    with patch("ncp_version.NCP") as mock_cls:
        mock_cls.return_value = Mock()
        mock_cls.return_value.run.return_value = fake
        run_ncp(FIXTURE_INPUT, repo_root=tmp_path, binary_override=None)
        mock_cls.assert_called_once_with()


def test_env_var_fallback_when_override_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NCP_RUNTIME", "/env/var/ncp")
    fake = _result(
        (_lookup_success(), _qualifier_success("qualified")),
        "Success",
        tmp_path,
    )
    with patch("ncp_version.NCP") as mock_cls:
        mock_cls.return_value = Mock()
        mock_cls.return_value.run.return_value = fake
        run_ncp(FIXTURE_INPUT, repo_root=tmp_path, binary_override=None)
        mock_cls.assert_called_once_with(binary="/env/var/ncp")
