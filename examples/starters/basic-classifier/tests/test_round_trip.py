# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori
"""End-to-end acceptance test for the basic-classifier starter.

Pipeline exercised:
  build.sh / build.ps1 produced dist/basic-classifier-0.1.0.tar.gz
  this test installs that archive in an isolated NCP_WORKFLOW_HOME,
  runs it through NCP.run_workflow() for each category, and asserts
  the business output matches the committed samples.

Workflow identity is DISCOVERED from workflow/graph.yaml so the test
continues to pass unchanged after a namespace rename (copy_rename.py).

Runtime binary resolution is EXPLICIT (test-scoped env var passed
through to NCP(binary=...) and install(..., binary=...)). NCP_RUNTIME
is NOT used; it is a test convention in the main SDK suite, not a
public SDK selection mechanism.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from ncp_runtime import NCP
from ncp_runtime.workflows import install

HERE = Path(__file__).resolve().parent
STARTER = HERE.parent
SAMPLES = STARTER / "samples"
ARCHIVE = STARTER / "dist" / "basic-classifier-0.1.0.tar.gz"

WORKFLOW_VERSION = "0.1.0"


def workflow_name() -> str:
    """Read the workflow's identity from graph.yaml (single source of truth).

    Avoids a hardcoded ID, so copy_rename.py's namespace change does not
    require a fourth manual edit to this file. No PyYAML dependency: we
    look for the one line that starts with `graph_id:`.
    """
    graph = STARTER / "workflow" / "graph.yaml"
    for line in graph.read_text(encoding="utf-8").splitlines():
        if line.startswith("graph_id:"):
            return line.partition(":")[2].strip().strip('"').strip("'")
    raise ValueError(f"missing graph_id in {graph}")


def _resolve_binary() -> Path:
    """Explicit binary resolution for the test. NOT a public SDK path."""
    env = os.environ.get("NCP_RUNTIME_BINARY")
    if not env:
        pytest.fail(
            "NCP_RUNTIME_BINARY must be set to the absolute path of a built "
            "ncp runtime binary. In CI this is set by the starter-validate "
            "workflow; locally see the starter README."
        )
    p = Path(env)
    if not p.is_file():
        pytest.fail(f"NCP_RUNTIME_BINARY points at a missing file: {p}")
    return p


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def isolated_workflow_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "ncp-workflow-home"
    monkeypatch.setenv("NCP_WORKFLOW_HOME", str(home))
    return home


@pytest.fixture
def installed(isolated_workflow_home: Path) -> Path:
    if not ARCHIVE.is_file():
        pytest.fail(
            f"archive not found at {ARCHIVE}. Run scripts/build.sh (or "
            f"scripts\\build.ps1) first. In CI this is the previous step."
        )
    binary = _resolve_binary()
    install(ARCHIVE, sha256=_sha256(ARCHIVE), binary=binary)
    return isolated_workflow_home


def test_archive_is_present_and_hashable():
    assert ARCHIVE.is_file(), f"starter build did not produce {ARCHIVE}"
    digest = _sha256(ARCHIVE)
    assert len(digest) == 64, "sha256 hex digest must be 64 chars"


def test_valid_sample_billing(installed: Path):
    binary = _resolve_binary()
    inputs = json.loads((SAMPLES / "valid.json").read_text(encoding="utf-8"))
    result = NCP(binary=binary).run_workflow(
        workflow_name(), inputs=inputs, version=WORKFLOW_VERSION
    )
    assert result.result_type == "Success", result.result_type
    assert result.terminals[0].output == {"category": "billing"}


def test_support_category(installed: Path):
    binary = _resolve_binary()
    result = NCP(binary=binary).run_workflow(
        workflow_name(),
        inputs={"text": "the site is broken, got an error 500"},
        version=WORKFLOW_VERSION,
    )
    assert result.result_type == "Success", result.result_type
    assert result.terminals[0].output == {"category": "support"}


def test_general_category(installed: Path):
    binary = _resolve_binary()
    result = NCP(binary=binary).run_workflow(
        workflow_name(),
        inputs={"text": "hello there"},
        version=WORKFLOW_VERSION,
    )
    assert result.result_type == "Success", result.result_type
    assert result.terminals[0].output == {"category": "general"}


def test_invalid_sample_fails_with_invalid_input(installed: Path):
    binary = _resolve_binary()
    inputs = json.loads((SAMPLES / "invalid.json").read_text(encoding="utf-8"))
    result = NCP(binary=binary).run_workflow(
        workflow_name(), inputs=inputs, version=WORKFLOW_VERSION
    )
    assert result.result_type == "Failure", result.result_type
    assert result.terminals[0].error is not None
    assert result.terminals[0].error.error_class == "INVALID_INPUT"
