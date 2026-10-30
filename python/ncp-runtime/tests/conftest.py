# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Pytest configuration + fixtures for ncp-runtime SDK tests.

Fixtures:

- :func:`repo_root` -- absolute Path to the NCP workspace root,
  derived from this test file's location (three directories up).
- :func:`fixtures_dir` -- absolute Path to the SDK's bundled
  ``_fixtures/`` directory, resolved via :mod:`importlib.resources`
  so the fixture works under both editable installs and wheel
  installs. Mirrors the resolution strategy in
  :mod:`ncp_runtime.demo`.
- :func:`fixture_graph` -- absolute Path to the bundled fixture
  graph at ``_fixtures/graph.yaml``.
- :func:`fixture_brick_dir` -- absolute Path to the brick-scan
  root. The fixture graph references the ``echo`` brick which
  lives at ``_fixtures/echo/``, so the brick directory IS the
  fixtures directory.
- :func:`ncp_binary` -- absolute Path to a built ``ncp`` runtime
  binary, resolved from the ``NCP_RUNTIME`` environment variable.
  Tests requiring a real subprocess depend on this fixture and
  skip cleanly when the env var is absent.

Gating discipline:

- SDK-owned paths (fixtures_dir, fixture_graph) FAIL
  unconditionally when absent. These are committed artifacts
  shipped with the SDK; their absence always indicates a broken
  source checkout or package installation, never a legitimate
  "integration not configured" state. A silent skip would mask
  a broken install as unconfigured integration.
- External infrastructure (``ncp_binary``) skips when
  ``NCP_RUNTIME`` is unset (unconfigured dev environment) and
  fails when the env var points to a path that is not a file
  (configured-but-broken). CI sets the variable in the
  ``python-sdk-test`` workflow job after building the release
  binary (File 25).
"""

from __future__ import annotations

import importlib.resources
import os
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute Path to the NCP workspace root.

    Derived from this test file's location: tests/conftest.py is
    at ``<root>/python/ncp-runtime/tests/conftest.py``, so three
    parents up gives the workspace root.
    """
    return Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Absolute Path to the SDK's bundled ``_fixtures/`` directory.

    Resolved via :mod:`importlib.resources` so the fixture works
    under both editable installs (``pip install -e .``) and wheel
    installs. Mirrors the resolution strategy in
    :mod:`ncp_runtime.demo`.

    Fails unconditionally when the directory is absent: this is
    an SDK-owned, committed artifact and its absence always
    indicates a broken checkout or install.
    """
    pkg = importlib.resources.files("ncp_runtime")
    path = Path(str(pkg.joinpath("_fixtures")))
    if not path.is_dir():
        pytest.fail(f"SDK _fixtures/ directory not found at {path}")
    return path


@pytest.fixture(scope="session")
def fixture_graph(fixtures_dir: Path) -> Path:
    """Absolute Path to the bundled fixture graph YAML.

    Fails unconditionally when the file is absent: SDK-owned,
    committed artifact.
    """
    path = fixtures_dir / "graph.yaml"
    if not path.is_file():
        pytest.fail(f"fixture graph not found at {path}")
    return path


@pytest.fixture(scope="session")
def fixture_brick_dir(fixtures_dir: Path) -> Path:
    """Absolute Path to the SDK's bundled brick-scan root.

    The fixture graph references the ``echo`` brick which lives
    at ``_fixtures/echo/``; the ``brick_dir`` the runtime scans
    is therefore ``_fixtures/`` itself. Named distinctly from
    :func:`fixtures_dir` so test code reads clearly as
    ``NCP().run(graph=..., brick_dir=fixture_brick_dir, ...)``.
    """
    return fixtures_dir


@pytest.fixture(scope="session")
def ncp_binary() -> Path:
    """Absolute Path to a built ``ncp`` runtime binary.

    Resolved from the ``NCP_RUNTIME`` environment variable. Tests
    requiring a real subprocess depend on this fixture and skip
    cleanly when the env var is absent (unconfigured dev
    environment). When the env var IS set but points to a
    non-file, the fixture fails loudly rather than silently
    skipping -- misconfiguration should surface in the CI report.

    CI sets the variable in the ``python-sdk-test`` workflow job
    after building the release binary.
    """
    raw = os.environ.get("NCP_RUNTIME")
    if not raw:
        pytest.skip(
            "NCP_RUNTIME env var not set; gated integration test "
            "(set it to the absolute path of a built ncp binary "
            "to run integration tests locally)"
        )
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        pytest.fail(f"NCP_RUNTIME points to {path} but it's not a file")
    return path
