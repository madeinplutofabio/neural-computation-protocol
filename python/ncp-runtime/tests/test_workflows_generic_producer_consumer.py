# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Delivery 4 producer/consumer capability test.

Uses the committed generic fixture at `examples/workflows/_test-generic/`
(single-brick echo workflow with a deliberately generic name,
`example.generic-echo`) to prove that the packager + installer work for
a workflow authored OUTSIDE the shipped NCP examples — i.e. that no
name-specific code path exists.

The test runs end-to-end pack -> install -> run_workflow against the
real NCP runtime. It skips in environments without NCP_RUNTIME set.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("yaml", reason="pyyaml required for workflow D4 test")

from ncp_runtime import NCP  # noqa: E402
from ncp_runtime.workflows import install, package  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
GENERIC_GRAPH = REPO_ROOT / "examples" / "workflows" / "_test-generic" / "graph.yaml"
BRICKS_DIR = REPO_ROOT / "examples" / "bricks"
NCP_BIN_ENV = os.environ.get("NCP_RUNTIME")


def _require_runtime() -> None:
    if not NCP_BIN_ENV:
        pytest.skip("NCP_RUNTIME not set; D4 test requires a real runtime binary")


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


def test_generic_workflow_round_trip(home: Path, dist: Path) -> None:
    """A workflow whose name is NOT one of the shipped NCP examples
    packages, installs, and runs end-to-end through the real runtime."""
    _require_runtime()
    out = dist / "example.generic-echo-0.1.0.tar.gz"
    pack = package(
        graph=GENERIC_GRAPH,
        brick_dir=BRICKS_DIR,
        name="example.generic-echo",
        version="0.1.0",
        runtime_compatibility=">=0.3.6,<0.4.0",
        description="D4 generic echo fixture",
        out=out,
    )
    assert out.is_file()
    assert pack["name"] == "example.generic-echo"
    assert len(pack["bricks"]) == 1
    assert pack["bricks"][0]["brick_id"] == "org.ncp-examples.echo"

    r = install(out, sha256=pack["sha256"], binary=NCP_BIN_ENV)
    assert r["install_root"] == home / "example.generic-echo" / "0.1.0"

    ncp = NCP(binary=Path(NCP_BIN_ENV) if NCP_BIN_ENV else None, timeout=20.0)
    payload = {"text": "hello from the generic fixture", "n": 42}
    res = ncp.run_workflow("example.generic-echo", payload, version="0.1.0")
    assert res.result_type == "Success"
    assert res.terminals, "expected at least one terminal"
    # Echo semantics: the brick returns the input verbatim.
    assert res.terminals[0].output == payload


def test_generic_workflow_side_by_side_versions(home: Path, dist: Path) -> None:
    """Install 0.1.0 and 0.2.0 of the same name; each selects correctly."""
    _require_runtime()
    for ver in ("0.1.0", "0.2.0"):
        pack = package(
            graph=GENERIC_GRAPH,
            brick_dir=BRICKS_DIR,
            name="example.generic-echo",
            version=ver,
            runtime_compatibility=">=0.3.6,<0.4.0",
            out=dist / f"example.generic-echo-{ver}.tar.gz",
        )
        install(pack["path"], sha256=pack["sha256"], binary=NCP_BIN_ENV)

    ncp = NCP(binary=Path(NCP_BIN_ENV) if NCP_BIN_ENV else None, timeout=20.0)
    r1 = ncp.run_workflow("example.generic-echo", {"v": "v1"}, version="0.1.0")
    r2 = ncp.run_workflow("example.generic-echo", {"v": "v2"}, version="0.2.0")
    assert r1.result_type == "Success"
    assert r2.result_type == "Success"
    assert r1.terminals[0].output == {"v": "v1"}
    assert r2.terminals[0].output == {"v": "v2"}
