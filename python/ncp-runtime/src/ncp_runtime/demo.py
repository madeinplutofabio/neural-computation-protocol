# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Onboarding smoke: `python -m ncp_runtime.demo`.

Resolves the ncp binary via the standard §3.1 precedence, runs the
bundled fixture graph at `_fixtures/graph.yaml` against the bundled
brick artifacts under `_fixtures/`, and prints `result_type` plus a
one-line summary per terminal.

Exit 0 on Success rollup; exit 1 on any other rollup or any
NCPSDKError.

Not a second framework: `NCP().run(...)` remains the primary API.
See design doc §12.
"""

from __future__ import annotations

import importlib.resources
import sys
from pathlib import Path
from typing import Any

from ncp_runtime import NCP, NCPSDKError
from ncp_runtime.discovery import resolve_binary


def _preview(value: object) -> str:
    """Bounded repr for a per-terminal summary line.

    Caps display at 80 chars with an ellipsis so terminal lines stay
    readable on a narrow console. Full data is still accessible via
    `result.terminals[i]` for programmatic consumers.
    """
    if value is None:
        return "None"
    s = repr(value)
    if len(s) > 80:
        return s[:77] + "..."
    return s


def main() -> int:
    # Locate bundled fixture graph + brick artifacts.
    try:
        pkg = importlib.resources.files("ncp_runtime")
        fixtures = pkg.joinpath("_fixtures")
        graph_path = Path(str(fixtures.joinpath("graph.yaml")))
        brick_dir = Path(str(fixtures))
    except (ModuleNotFoundError, FileNotFoundError) as e:
        print(
            f"demo: cannot locate bundled fixtures ({type(e).__name__}: {e})",
            file=sys.stderr,
        )
        return 1

    if not graph_path.is_file():
        print(
            f"demo: bundled fixture graph not found at {graph_path}",
            file=sys.stderr,
        )
        return 1

    # Resolve binary via the public discovery helper. The absolute
    # path printed below makes the §3.1 precedence step visible to a
    # reader of the demo output: a path inside the installed wheel's
    # `_bundled/` directory means the bundled-binary step succeeded;
    # a path elsewhere means it was found on PATH. The demo passes
    # no override, so the user-override step is not exercised here.
    try:
        binary_path = resolve_binary(None)
    except NCPSDKError as e:
        print(f"demo: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(f"demo: binary: {binary_path}")

    inputs: dict[str, Any] = {"message": "hello"}

    # Pass the resolved binary explicitly so the demo's run uses the
    # same binary that was reported above. The authoritative
    # runtime-version check happens inside the first .run() per the
    # standard SDK lifecycle; the demo deliberately does not
    # duplicate it here.
    ncp = NCP(binary=binary_path)
    try:
        with ncp.run(graph=graph_path, brick_dir=brick_dir, inputs=inputs) as result:
            print(f"demo: result_type: {result.result_type}")
            for term in result.terminals:
                print(
                    f"demo: terminal node={term.node_id} "
                    f"brick={term.brick_id} step={term.step} "
                    f"type={term.result_type} "
                    f"output={_preview(term.output)} "
                    f"error={_preview(term.error)}"
                )
            return 0 if result.result_type == "Success" else 1
    except NCPSDKError as e:
        print(f"demo: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
