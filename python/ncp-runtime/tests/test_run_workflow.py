# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Delivery 3 tests: NCP.run_workflow() delegates to NCP.run() against
the installed workflow resolved by workflows.install.resolve().
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("yaml", reason="pyyaml required for run_workflow tests")

from ncp_runtime import NCP  # noqa: E402
from ncp_runtime.types import (  # noqa: E402
    NCPWorkflowAmbiguousVersionError,
    NCPWorkflowNotInstalledError,
)
from ncp_runtime.workflows import install, package  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
LEAD_QUAL_GRAPH = REPO_ROOT / "examples" / "graphs" / "lead-qualification" / "graph.yaml"
LEAD_QUAL_SAMPLES = REPO_ROOT / "examples" / "graphs" / "lead-qualification" / "samples"
BRICKS_DIR = REPO_ROOT / "examples" / "bricks"
NCP_BIN_ENV = os.environ.get("NCP_RUNTIME")


def _require_runtime() -> None:
    if not NCP_BIN_ENV:
        pytest.skip("NCP_RUNTIME not set; test requires a real runtime binary")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "wfhome"
    monkeypatch.setenv("NCP_WORKFLOW_HOME", str(h))
    return h


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    d = tmp_path / "dist"
    d.mkdir()
    return d


def _install_lead_qual(dist: Path, *, version: str = "0.1.0") -> dict:
    pack = package(
        graph=LEAD_QUAL_GRAPH,
        brick_dir=BRICKS_DIR,
        name="org.ncp-examples.lead-qualification",
        version=version,
        runtime_compatibility=">=0.4.0,<0.5.0",
        out=dist / f"lead-qualification-{version}.tar.gz",
    )
    return install(pack["path"], sha256=pack["sha256"], binary=NCP_BIN_ENV)


def test_run_workflow_matches_path_based_run_byte_for_byte(home: Path, dist: Path) -> None:
    """Running an installed workflow by name yields the same Result.terminals[0]
    .output as calling NCP.run() with the repo graph + brick_dir."""
    _require_runtime()
    _install_lead_qual(dist, version="0.1.0")
    sample = LEAD_QUAL_SAMPLES / "qualified_2_of_3.json"
    if not sample.is_file():
        pytest.skip(f"sample input missing: {sample}")
    inputs = json.loads(sample.read_text(encoding="utf-8"))
    ncp = NCP(binary=Path(NCP_BIN_ENV) if NCP_BIN_ENV else None, timeout=20.0)

    baseline = ncp.run(graph=LEAD_QUAL_GRAPH, brick_dir=BRICKS_DIR, inputs=inputs)
    by_name = ncp.run_workflow("org.ncp-examples.lead-qualification", inputs, version="0.1.0")

    assert baseline.result_type == "Success"
    assert by_name.result_type == "Success"
    assert baseline.terminals[0].output == by_name.terminals[0].output


def test_run_workflow_version_none_single_install(home: Path, dist: Path) -> None:
    _require_runtime()
    _install_lead_qual(dist, version="0.1.0")
    sample = LEAD_QUAL_SAMPLES / "qualified_2_of_3.json"
    if not sample.is_file():
        pytest.skip(f"sample input missing: {sample}")
    inputs = json.loads(sample.read_text(encoding="utf-8"))
    ncp = NCP(binary=Path(NCP_BIN_ENV) if NCP_BIN_ENV else None, timeout=20.0)

    res = ncp.run_workflow("org.ncp-examples.lead-qualification", inputs)
    assert res.result_type == "Success"


def test_run_workflow_ambiguous_version_raises(home: Path, dist: Path) -> None:
    _require_runtime()
    _install_lead_qual(dist, version="0.1.0")
    _install_lead_qual(dist, version="0.2.0")
    ncp = NCP(binary=Path(NCP_BIN_ENV) if NCP_BIN_ENV else None, timeout=20.0)
    with pytest.raises(NCPWorkflowAmbiguousVersionError):
        ncp.run_workflow("org.ncp-examples.lead-qualification", {})


def test_run_workflow_version_not_installed_raises(home: Path, dist: Path) -> None:
    _require_runtime()
    _install_lead_qual(dist, version="0.1.0")
    ncp = NCP(binary=Path(NCP_BIN_ENV) if NCP_BIN_ENV else None, timeout=20.0)
    with pytest.raises(NCPWorkflowNotInstalledError):
        ncp.run_workflow("org.ncp-examples.lead-qualification", {}, version="99.0.0")


def test_run_workflow_name_not_installed_raises(home: Path) -> None:
    # No runtime needed: the resolve() step fails before any subprocess.
    ncp = NCP(timeout=5.0)
    with pytest.raises(NCPWorkflowNotInstalledError):
        ncp.run_workflow("org.not.present", {})


def test_run_workflow_specific_version_selection(home: Path, dist: Path) -> None:
    _require_runtime()
    _install_lead_qual(dist, version="0.1.0")
    _install_lead_qual(dist, version="0.2.0")
    sample = LEAD_QUAL_SAMPLES / "qualified_2_of_3.json"
    if not sample.is_file():
        pytest.skip(f"sample input missing: {sample}")
    inputs = json.loads(sample.read_text(encoding="utf-8"))
    ncp = NCP(binary=Path(NCP_BIN_ENV) if NCP_BIN_ENV else None, timeout=20.0)
    # Both versions were packed identically; they must both produce success.
    r1 = ncp.run_workflow("org.ncp-examples.lead-qualification", inputs, version="0.1.0")
    r2 = ncp.run_workflow("org.ncp-examples.lead-qualification", inputs, version="0.2.0")
    assert r1.result_type == "Success"
    assert r2.result_type == "Success"
    assert r1.terminals[0].output == r2.terminals[0].output
