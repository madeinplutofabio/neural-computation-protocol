# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Tests for ``python -m ncp_runtime.demo`` (§12 onboarding entry).

Two sections:

  * Section 1 (requires ``ncp_binary``): the primary subprocess
    test. Invokes ``python -m ncp_runtime.demo`` with the real
    pinned binary on PATH; asserts exit 0 + expected stdout
    patterns per §12 (resolve-binary line, result_type line,
    per-terminal summary with echoed output). This is the locked
    integration contract from File 15 approval.
  * Section 2 (no ``ncp_binary`` needed): direct ``main()`` unit
    tests for the two degraded exit paths -- binary-not-found and
    fixture-graph-missing. These are hard to exercise via
    subprocess: PATH isolation is unreliable cross-platform
    (Windows needs SYSTEMROOT, systems with ``ncp`` installed
    elsewhere pollute the lookup) and corrupting the installed
    package to delete ``graph.yaml`` is destructive. Direct
    ``main()`` + ``monkeypatch`` is cleaner.
"""

from __future__ import annotations

import importlib.resources
import os
import subprocess
import sys
from pathlib import Path

import pytest

from ncp_runtime import demo
from ncp_runtime.types import NCPBinaryNotFoundError

# ======================================================================
# Section 1: Subprocess integration (happy path)
# ======================================================================


def test_demo_subprocess_exits_zero_against_pinned_runtime(
    ncp_binary: Path,
) -> None:
    """``python -m ncp_runtime.demo`` runs successfully when the
    pinned ``ncp`` binary is discoverable on PATH.

    PATH-prepend puts ncp's directory first so
    ``shutil.which("ncp")`` (demo's precedence step 3) resolves
    to our fixture binary even if the system has a different
    ``ncp`` installed elsewhere. Preserves the rest of the parent
    environment so Python's own DLL / system-path lookups
    continue to work on Windows.
    """
    env = {
        **os.environ,
        "PATH": (f"{ncp_binary.parent}{os.pathsep}{os.environ.get('PATH', '')}"),
    }
    result = subprocess.run(
        [sys.executable, "-m", "ncp_runtime.demo"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"demo exited {result.returncode};\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "demo: binary:" in result.stdout
    assert "demo: result_type: Success" in result.stdout
    assert "demo: terminal node=echo" in result.stdout
    assert "brick=org.ncp-examples.echo" in result.stdout
    assert "type=Success" in result.stdout
    # The echoed output appears via _preview(repr(dict)).
    assert "{'message': 'hello'}" in result.stdout


# ======================================================================
# Section 2: Direct main() unit tests for degraded exit paths
# ======================================================================


def test_demo_main_exits_nonzero_when_binary_not_found(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When ``resolve_binary`` raises ``NCPBinaryNotFoundError``,
    demo exits 1 and prints the error type + message to stderr.
    """

    def failing_resolve(user_override: object) -> Path:
        raise NCPBinaryNotFoundError("no binary found anywhere")

    monkeypatch.setattr("ncp_runtime.demo.resolve_binary", failing_resolve)

    exit_code = demo.main()
    assert exit_code == 1

    captured = capsys.readouterr()
    assert "NCPBinaryNotFoundError" in captured.err
    assert "no binary found anywhere" in captured.err


def test_demo_main_exits_nonzero_when_fixture_graph_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When the SDK's bundled ``_fixtures/graph.yaml`` is absent
    (broken install), demo detects the missing file via
    ``graph_path.is_file()`` and exits 1 with a stderr message
    identifying the missing fixture.

    Patches ``importlib.resources.files`` to return an empty
    fake package directory. ``Path`` supports the same
    ``joinpath`` / ``str()`` interface the Traversable protocol
    provides, so demo's resolution code runs end-to-end against
    the fake tree.

    Also patches ``resolve_binary`` to a no-op even though demo
    currently checks the fixture BEFORE resolving the binary --
    defensive isolation so a future reorder of demo's checks
    (e.g., "fail fast on missing binary before filesystem work")
    cannot silently flip this test from "fixture missing" to
    "binary not found" and break the stderr-message assertion.
    """
    fake_pkg = tmp_path / "fake_ncp_runtime"
    (fake_pkg / "_fixtures").mkdir(parents=True)

    monkeypatch.setattr(importlib.resources, "files", lambda name: fake_pkg)
    monkeypatch.setattr(
        "ncp_runtime.demo.resolve_binary",
        lambda user_override: tmp_path / "fake-ncp",
    )

    exit_code = demo.main()
    assert exit_code == 1

    captured = capsys.readouterr()
    assert "fixture graph not found" in captured.err
