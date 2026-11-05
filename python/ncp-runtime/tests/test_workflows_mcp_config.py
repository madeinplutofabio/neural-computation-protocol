# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Delivery 3 tests: `mcp-config` emits a correct configuration record for
an installed workflow. The MCP server is not modified; these tests only
exercise the configuration generator.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytest.importorskip("yaml", reason="pyyaml required for mcp-config tests")

from ncp_runtime.types import (  # noqa: E402
    NCPWorkflowAmbiguousVersionError,
    NCPWorkflowNotInstalledError,
)
from ncp_runtime.workflows import (  # noqa: E402
    install,
    mcp_config,
    mcp_config_json,
    mcp_config_snippet,
    package,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
LEAD_QUAL_GRAPH = REPO_ROOT / "examples" / "graphs" / "lead-qualification" / "graph.yaml"
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


def test_mcp_config_returns_graph_and_brick_dir(home: Path, dist: Path) -> None:
    _require_runtime()
    r = _install_lead_qual(dist)
    cfg = mcp_config("org.ncp-examples.lead-qualification", version="0.1.0")
    assert cfg["command"] == "ncp-mcp-server"
    assert cfg["args"][0] == "--graph"
    assert cfg["args"][1] == str(r["install_root"] / "graph.yaml")
    assert cfg["args"][2] == "--brick-dir"
    assert cfg["args"][3] == str(r["install_root"] / "bricks")
    assert cfg["env"] == {}
    assert cfg["name"] == "org.ncp-examples.lead-qualification"
    assert cfg["version"] == "0.1.0"


def test_mcp_config_custom_server_command(home: Path, dist: Path) -> None:
    _require_runtime()
    _install_lead_qual(dist)
    cfg = mcp_config(
        "org.ncp-examples.lead-qualification",
        version="0.1.0",
        server_command="/custom/path/ncp-mcp-server",
    )
    assert cfg["command"] == "/custom/path/ncp-mcp-server"


def test_mcp_config_json_parses_back(home: Path, dist: Path) -> None:
    _require_runtime()
    _install_lead_qual(dist)
    text = mcp_config_json("org.ncp-examples.lead-qualification", version="0.1.0")
    parsed = json.loads(text)
    assert parsed["command"] == "ncp-mcp-server"
    assert "--graph" in parsed["args"]
    assert "--brick-dir" in parsed["args"]


def test_mcp_config_snippet_shape(home: Path, dist: Path) -> None:
    _require_runtime()
    _install_lead_qual(dist)
    text = mcp_config_snippet("org.ncp-examples.lead-qualification", version="0.1.0")
    parsed = json.loads(text)
    assert "mcpServers" in parsed
    entry = parsed["mcpServers"]["org.ncp-examples.lead-qualification"]
    assert entry["command"] == "ncp-mcp-server"
    assert "--graph" in entry["args"]
    assert "--brick-dir" in entry["args"]


def test_mcp_config_ambiguous_version_raises(home: Path, dist: Path) -> None:
    _require_runtime()
    _install_lead_qual(dist, version="0.1.0")
    _install_lead_qual(dist, version="0.2.0")
    with pytest.raises(NCPWorkflowAmbiguousVersionError):
        mcp_config("org.ncp-examples.lead-qualification")


def test_mcp_config_name_not_installed_raises(home: Path) -> None:
    with pytest.raises(NCPWorkflowNotInstalledError):
        mcp_config("org.not.present")
