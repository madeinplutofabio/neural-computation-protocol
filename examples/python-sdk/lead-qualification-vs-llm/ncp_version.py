# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""NCP-side execution adapter for the lead-qualification-vs-llm example.

Takes a {company_url, target_icp, notes} input dict and runs the
`org.ncp-examples.lead-qualification` graph via the ncp-runtime Python
SDK. Returns an AdapterResult (defined in scorer.py) carrying the
normalized ImplResult (consumed by scorer.score_case) plus the
measurement fields compare.py serializes into comparison.json
per-case entries.

See CONTRACT.md:
  * §6.1 — runtime axes and their NCP sources
  * §6.2 — observable-and-input-specific rejection invariant
  * §9.2 — NCP side protocol and binary-resolution convention
  * §9.4 — per-case measurement fields (incl. graph_invocations semantic)

Exception-handling policy (per File 4 scope review):
  * Expected operational failures from the SDK (NCPSDKError subclasses
    raised anywhere inside NCP.run) are caught and reported as
    runtime="unresolved" with a diagnostic.
  * Programmer-level errors (TypeError, AttributeError, bugs in result
    processing) are NOT caught — they propagate visibly so a broken
    adapter cannot produce a plausible-looking 50-case report of
    unresolved outcomes.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

from ncp_runtime import NCP, NCPSDKError, Result

# Local import — scorer.py sits next to this adapter file.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scorer import AdapterResult, ImplResult  # noqa: E402

GRAPH_RELPATH = "examples/graphs/lead-qualification/graph.yaml"
BRICK_DIR_RELPATH = "examples/bricks/"
QUALIFIER_NODE_ID = "qualify"

# §5.2 business labels. Any other next_action value from a qualifier
# terminal is treated as an extraction failure (runtime="unresolved").
_VALID_BUSINESS_LABELS = frozenset({"qualified", "not_qualified", "needs_enrichment"})

NCP_RUNTIME_ENV_VAR = "NCP_RUNTIME"


def _build_ncp(binary_override: str | None) -> NCP:
    """Construct an NCP() per CONTRACT.md §9.2 binary-resolution rules."""
    if binary_override:
        return NCP(binary=binary_override)
    return NCP()


def _extract_business_label(result: Result) -> str | None:
    """Pull next_action from the qualifier terminal if present + well-formed.

    Returns one of the three §5.2 business labels or None if the result's
    terminals do not contain a well-formed qualifier terminal. Never
    raises on data-shape issues (returns None instead); any unexpected
    exception (programmer error in SDK internals) propagates.
    """
    for terminal in result.terminals:
        if terminal.node_id != QUALIFIER_NODE_ID:
            continue
        if terminal.result_type != "Success":
            return None
        output = terminal.output
        if not isinstance(output, dict):
            return None
        next_action = output.get("next_action")
        if isinstance(next_action, str) and next_action in _VALID_BUSINESS_LABELS:
            return next_action
        return None
    return None


def _any_terminal_has_invalid_input(result: Result) -> bool:
    """§6.1 NCP source for validation='invalid_input'.

    True iff any terminal reports Failure with error_class=INVALID_INPUT.
    """
    for terminal in result.terminals:
        if terminal.result_type != "Failure":
            continue
        err = terminal.error
        if err is not None and err.error_class == "INVALID_INPUT":
            return True
    return False


def _mk_result(
    *,
    impl: ImplResult,
    latency_ms: float,
    graph_invocations: int,
    error: str | None,
) -> AdapterResult:
    """AdapterResult factory; NCP-side token/cost fields are always 0."""
    return AdapterResult(
        impl_result=impl,
        latency_ms=latency_ms,
        model_api_calls=0,
        graph_invocations=graph_invocations,
        tokens_in=0,
        tokens_out=0,
        model_cost_usd=0.0,
        llm_model_resolved=None,
        error=error,
    )


def _process_result(
    result: Result,
    latency_ms: float,
    graph_invocations: int,
) -> AdapterResult:
    """Map a returned Result into an AdapterResult.

    Called from inside the try/finally that ensures result.cleanup() runs
    regardless of outcome. Any unexpected exception here bubbles up as
    a programmer error (not caught at the adapter boundary).
    """
    rt = result.result_type

    if rt == "Failure":
        validation = "invalid_input" if _any_terminal_has_invalid_input(result) else None
        return _mk_result(
            impl=ImplResult(
                runtime="failure",
                validation=validation,  # type: ignore[arg-type]
                business=None,
            ),
            latency_ms=latency_ms,
            graph_invocations=graph_invocations,
            error=None,
        )

    if rt == "Success":
        business = _extract_business_label(result)
        if business is None:
            return _mk_result(
                impl=ImplResult(runtime="unresolved", validation=None, business=None),
                latency_ms=latency_ms,
                graph_invocations=graph_invocations,
                error="qualifier terminal missing or malformed next_action",
            )
        return _mk_result(
            impl=ImplResult(
                runtime="success",
                validation=None,
                business=business,  # type: ignore[arg-type]
            ),
            latency_ms=latency_ms,
            graph_invocations=graph_invocations,
            error=None,
        )

    # LowConfidence or other unexpected rollup. The icp-qualifier brick
    # emits only Success/Failure terminals (confirmed against
    # bricks/icp-qualifier/src/lib.rs); defensive handling for any
    # future SDK/graph change that produces LowConfidence or similar.
    return _mk_result(
        impl=ImplResult(runtime="unresolved", validation=None, business=None),
        latency_ms=latency_ms,
        graph_invocations=graph_invocations,
        error=f"unexpected rollup result_type={rt!r} (graph defines only Success/Failure)",
    )


def run_ncp(
    input_dict: dict[str, Any],
    *,
    repo_root: Path,
    binary_override: str | None = None,
) -> AdapterResult:
    """Execute one fixture through the NCP graph; return AdapterResult.

    Latency timing surrounds both NCP() construction and ncp.run() —
    anything the adapter attributes to "the NCP side."
    """
    graph_path = repo_root / GRAPH_RELPATH
    brick_dir = repo_root / BRICK_DIR_RELPATH

    if binary_override is None:
        env_val = os.environ.get(NCP_RUNTIME_ENV_VAR)
        if env_val:
            binary_override = env_val

    start = time.perf_counter()
    # §9.4 semantic: graph_invocations counts calls submitted to
    # NCP.run(). Starts at 0; bumps to 1 immediately after construction
    # succeeds and before ncp.run() is called. Any SDK exception from
    # ncp.run() leaves it at 1 (we attempted the call).
    graph_invocations = 0

    try:
        ncp = _build_ncp(binary_override)
        graph_invocations = 1
        result = ncp.run(
            graph=graph_path,
            brick_dir=brick_dir,
            inputs=input_dict,
        )
    except NCPSDKError as exc:
        # Expected operational failure. Covers binary-not-found,
        # version-probe timeout, graph-execution timeout, subprocess
        # error, trace schema error — the SDK's documented failure
        # modes. Non-SDK exceptions (programmer errors) are not
        # caught here; see module docstring.
        return _mk_result(
            impl=ImplResult(runtime="unresolved", validation=None, business=None),
            latency_ms=(time.perf_counter() - start) * 1000.0,
            graph_invocations=graph_invocations,
            error=f"{type(exc).__name__}: {exc}",
        )

    latency_ms = (time.perf_counter() - start) * 1000.0

    # Process the result inside a try/finally so cleanup runs even if
    # processing raises an unexpected exception. The exception itself
    # is NOT caught here — programmer errors must stay visible.
    try:
        return _process_result(result, latency_ms, graph_invocations)
    finally:
        try:
            result.cleanup()
        except OSError:
            # Explicit cleanup failed (e.g., Windows file-in-use). Not
            # fatal; the SDK's weakref.finalize is a backstop.
            pass
