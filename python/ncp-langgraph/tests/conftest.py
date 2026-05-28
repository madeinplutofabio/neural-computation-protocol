# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Pytest configuration + fixtures for ncp-langgraph tests.

Fixtures:

- :func:`repo_root` -- absolute Path to the NCP workspace root,
  derived from this test file's location (four directories up).
- :func:`echo_pipeline_graph` -- absolute Path to the bundled
  echo-pipeline graph YAML at
  ``examples/graphs/echo-pipeline/graph.yaml``.
- :func:`brick_dir` -- absolute Path to the bundled bricks directory
  at ``examples/bricks/``.
- :func:`mcp_server_binary` -- absolute Path to a built
  ``ncp-mcp-server`` binary, resolved from the ``NCP_MCP_SERVER``
  environment variable. Tests requiring a real subprocess gate on
  this fixture and skip cleanly when the env var is absent.

Integration tests gate on ``mcp_server_binary``; CI sets the env var
after building the release binary (see the ``langgraph-test`` job in
``.github/workflows/rust.yml``). Local developers can either build
the binary and export the env var, or skip the gated tests.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _skip_or_fail_missing(message: str) -> None:
    """Skip when integration is not configured; fail when it is.

    Integration tests are gated on ``NCP_MCP_SERVER``. When the env
    var is absent, missing example paths are a "this developer hasn't
    set up integration tests yet" case -- skip cleanly. When the env
    var IS set (CI, or a developer running integration tests), missing
    example paths are a "the gated tests SHOULD run but a fixture path
    is broken" case -- fail loudly so the misconfiguration is visible
    in the CI report instead of silently skipping every integration
    test.
    """
    if os.environ.get("NCP_MCP_SERVER"):
        pytest.fail(message)
    pytest.skip(message)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute Path to the NCP workspace root.

    Derived from this test file's location: tests/conftest.py is at
    ``<root>/python/ncp-langgraph/tests/conftest.py``, so four parents
    up gives the workspace root.
    """
    return Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def echo_pipeline_graph(repo_root: Path) -> Path:
    """Absolute Path to the bundled echo-pipeline graph YAML."""
    path = repo_root / "examples" / "graphs" / "echo-pipeline" / "graph.yaml"
    if not path.is_file():
        _skip_or_fail_missing(f"echo-pipeline graph not found at {path}")
    return path


@pytest.fixture(scope="session")
def brick_dir(repo_root: Path) -> Path:
    """Absolute Path to the bundled bricks directory."""
    path = repo_root / "examples" / "bricks"
    if not path.is_dir():
        _skip_or_fail_missing(f"bricks directory not found at {path}")
    return path


@pytest.fixture(scope="session")
def mcp_server_binary() -> Path:
    """Absolute Path to a built ``ncp-mcp-server`` binary.

    Resolved from the ``NCP_MCP_SERVER`` environment variable. Tests
    requiring a real subprocess depend on this fixture and skip
    cleanly when the env var is absent. CI sets the variable in the
    ``langgraph-test`` workflow job after building the release binary.
    """
    raw = os.environ.get("NCP_MCP_SERVER")
    if not raw:
        pytest.skip(
            "NCP_MCP_SERVER env var not set; gated integration test "
            "(set it to the absolute path of a built ncp-mcp-server "
            "binary to run integration tests locally)"
        )
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        # Once NCP_MCP_SERVER is set, integration is explicitly
        # configured. A bad path is misconfiguration that should fail
        # CI, not silently skip every integration test. Same discipline
        # applied to echo_pipeline_graph and brick_dir.
        pytest.fail(f"NCP_MCP_SERVER points to {path} but it's not a file")
    return path
