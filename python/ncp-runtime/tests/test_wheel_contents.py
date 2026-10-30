# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Wheel + sdist content assertions + isolated-wheel-install smoke.

Three sections:

  * Section 1 (fast, no ``ncp_binary``): wheel content
    assertions. ``py.typed`` is zero bytes; bundled fixture
    graph and brick manifest match the committed source
    byte-for-byte; bundled wasm matches the manifest's declared
    13846-byte / sha256 ``b5cc1a3...b512973`` fixture; ``tests/``
    is NOT in the wheel.
  * Section 2 (fast, no ``ncp_binary``): sdist content
    assertions. Source tree + tests + fixtures all ship.
    Wheel-only artifacts (``_bundled/ncp``, ``_bundled/ncp.exe``,
    ``_bundled/.no_binary``, ``_bundled/provenance.json``) do
    NOT appear in the sdist.
  * Section 3 (slow, requires ``ncp_binary``): isolated-wheel-
    install smoke. Fresh venv -> ``pip install <wheel>`` ->
    assert ``ncp_runtime.__file__`` resolves inside the venv's
    site-packages (NOT the editable source tree) -> run
    ``python -m ncp_runtime.demo`` with cwd=tmp_path and
    PYTHONPATH removed.

A session-scoped fixture builds the wheel + sdist once via
``python -m build``; individual tests inspect the artifacts.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

# Expected echo.wasm size + sha256, mirrored from
# examples/bricks/echo/manifest.yaml. These are the committed
# binary's own digest -- hardcoded because the wasm is a built
# artifact, not a text file the test can diff against.
_EXPECTED_WASM_SIZE = 13846
_EXPECTED_WASM_SHA256 = "b5cc1a32634ac05ba0d208b9961179fd8b1cc298c7afa913cb59f6018b512973"


@pytest.fixture(scope="session")
def built_artifacts(repo_root: Path, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Build the wheel + sdist once per test session.

    Uses ``python -m build`` (PEP 517). The build backend
    (hatchling) and ``build`` itself are in the dev extras, so
    they are available after ``pip install -e ".[dev]"``.
    """
    build_dir = tmp_path_factory.mktemp("build")
    pkg_dir = repo_root / "python" / "ncp-runtime"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--sdist",
            "--outdir",
            str(build_dir),
            str(pkg_dir),
        ],
        check=True,
        timeout=180,
    )
    wheels = list(build_dir.glob("*.whl"))
    sdists = list(build_dir.glob("*.tar.gz"))
    assert len(wheels) == 1, f"expected 1 wheel, got {wheels}"
    assert len(sdists) == 1, f"expected 1 sdist, got {sdists}"
    return {"wheel": wheels[0], "sdist": sdists[0]}


@pytest.fixture(scope="session")
def source_fixtures_dir(repo_root: Path) -> Path:
    """Absolute Path to the committed source ``_fixtures/``
    directory, used as the "expected bytes" ground truth for
    Section 1 byte-equality assertions.
    """
    return repo_root / "python" / "ncp-runtime" / "src" / "ncp_runtime" / "_fixtures"


# ======================================================================
# Section 1: Wheel content assertions
# ======================================================================


def test_wheel_py_typed_is_empty(
    built_artifacts: dict[str, Path],
) -> None:
    """PEP 561 marker is a zero-byte file. If the marker ever
    acquires content, adopters' mypy runs may still see the
    SDK's types but we've silently violated the PEP 561 shape
    contract.
    """
    with zipfile.ZipFile(built_artifacts["wheel"]) as z:
        assert z.read("ncp_runtime/py.typed") == b""


def test_wheel_fixture_graph_matches_source(
    built_artifacts: dict[str, Path],
    source_fixtures_dir: Path,
) -> None:
    """The bundled graph.yaml in the wheel must match the
    committed source byte-for-byte. Source bytes are the ground
    truth (established at File 15 approval); we don't hardcode
    a digest that would need re-generation on legitimate edits
    to the fixture graph.
    """
    source_bytes = (source_fixtures_dir / "graph.yaml").read_bytes()
    with zipfile.ZipFile(built_artifacts["wheel"]) as z:
        wheel_bytes = z.read("ncp_runtime/_fixtures/graph.yaml")
    assert wheel_bytes == source_bytes, (
        f"graph.yaml in wheel differs from source; "
        f"source sha256={hashlib.sha256(source_bytes).hexdigest()}, "
        f"wheel sha256={hashlib.sha256(wheel_bytes).hexdigest()}"
    )


def test_wheel_fixture_brick_manifest_matches_source(
    built_artifacts: dict[str, Path],
    source_fixtures_dir: Path,
) -> None:
    """The bundled brick manifest.yaml must match the committed
    source byte-for-byte. The declared ``digest`` and
    ``size_bytes`` fields describe the wasm; mangling the
    manifest text would break the runtime's resolver even
    though the wasm digest would still match.
    """
    source_bytes = (source_fixtures_dir / "echo" / "manifest.yaml").read_bytes()
    with zipfile.ZipFile(built_artifacts["wheel"]) as z:
        wheel_bytes = z.read("ncp_runtime/_fixtures/echo/manifest.yaml")
    assert wheel_bytes == source_bytes, (
        f"echo/manifest.yaml in wheel differs from source; "
        f"source sha256={hashlib.sha256(source_bytes).hexdigest()}, "
        f"wheel sha256={hashlib.sha256(wheel_bytes).hexdigest()}"
    )


def test_wheel_fixture_wasm_matches_manifest_digest(
    built_artifacts: dict[str, Path],
) -> None:
    """The bundled echo.wasm must match its manifest-declared
    size + sha256 digest. If hatchling mangles binary bytes
    during the build, this digest flips and the runtime's
    resolver would reject the wasm at execution time -- caught
    here at build time instead.
    """
    with zipfile.ZipFile(built_artifacts["wheel"]) as z:
        wasm_data = z.read("ncp_runtime/_fixtures/echo/echo.wasm")
    assert len(wasm_data) == _EXPECTED_WASM_SIZE
    assert hashlib.sha256(wasm_data).hexdigest() == _EXPECTED_WASM_SHA256


def test_wheel_excludes_tests(built_artifacts: dict[str, Path]) -> None:
    """``tests/`` is a development-only directory; the wheel's
    ``packages = ["src/ncp_runtime"]`` scope should not include
    it. If tests end up in the wheel, an unrelated change has
    broken that scoping.
    """
    with zipfile.ZipFile(built_artifacts["wheel"]) as z:
        names = z.namelist()
    assert not any(n.startswith("tests/") for n in names), (
        f"tests/ leaked into wheel: {[n for n in names if n.startswith('tests/')]}"
    )


# ======================================================================
# Section 2: Sdist content assertions
# ======================================================================


def _sdist_names(sdist_path: Path) -> list[str]:
    """Return every entry's name within the sdist tarball,
    stripped of the top-level ``<name>-<version>/`` prefix for
    direct substring matching.
    """
    with tarfile.open(sdist_path, "r:gz") as t:
        all_names = t.getnames()
    stripped: list[str] = []
    for n in all_names:
        parts = n.split("/", 1)
        stripped.append(parts[1] if len(parts) == 2 else n)
    return stripped


def test_sdist_contains_source_and_fixtures(
    built_artifacts: dict[str, Path],
) -> None:
    names = _sdist_names(built_artifacts["sdist"])
    assert "src/ncp_runtime/__init__.py" in names
    assert "src/ncp_runtime/runner.py" in names
    assert "src/ncp_runtime/py.typed" in names
    assert "src/ncp_runtime/_fixtures/graph.yaml" in names
    assert "src/ncp_runtime/_fixtures/echo/manifest.yaml" in names
    assert "src/ncp_runtime/_fixtures/echo/echo.wasm" in names


def test_sdist_contains_tests(built_artifacts: dict[str, Path]) -> None:
    """Adopters who build from sdist should be able to run the
    test suite without needing a separate checkout. The sdist
    ``include`` in pyproject.toml lists ``tests`` for this.
    """
    names = _sdist_names(built_artifacts["sdist"])
    assert "tests/conftest.py" in names
    assert "tests/test_smoke.py" in names


def test_sdist_excludes_bundled_binary_artifacts(
    built_artifacts: dict[str, Path],
) -> None:
    """The ``_bundled/*`` artifacts (``ncp``, ``ncp.exe``,
    ``.no_binary``, ``provenance.json``) are wheel-build-time
    outputs scoped to the wheel target in pyproject.toml. They
    MUST NOT appear in the sdist, which is a source
    distribution. Shipping them in the sdist would mean a user
    who built from sdist got a binary they didn't ask for AND
    can't easily audit.
    """
    names = _sdist_names(built_artifacts["sdist"])
    forbidden_suffixes = (
        "/ncp",
        "/ncp.exe",
        "/.no_binary",
        "/provenance.json",
    )
    for name in names:
        if "/_bundled/" in name:
            for suffix in forbidden_suffixes:
                assert not name.endswith(suffix), (
                    f"sdist contains wheel-only bundled artifact "
                    f"{name!r}; see pyproject.toml "
                    f"[tool.hatch.build.targets.wheel].artifacts "
                    f"scoping"
                )


# ======================================================================
# Section 3: Isolated-wheel-install smoke
# ======================================================================


def test_wheel_install_demo_runs_from_fresh_venv(
    built_artifacts: dict[str, Path],
    ncp_binary: Path,
    tmp_path: Path,
) -> None:
    """Install the built wheel into a fresh venv, prove the venv
    import resolves inside its own site-packages (not the
    editable source tree), then run ``python -m ncp_runtime.demo``
    from the venv interpreter with cwd pinned to tmp_path and
    PYTHONPATH removed.

    Three defenses against source-tree import contamination,
    without which the test could falsely pass against the
    editable install:
      1. ``env.pop("PYTHONPATH", ...)``: prevents inherited
         PYTHONPATH from exposing the source tree.
      2. ``cwd=tmp_path``: runs the subprocess from a neutral
         directory, not from the project root.
      3. Precondition check: assert ``ncp_runtime.__file__``
         resolves inside ``venv_dir``, failing loudly if the
         import somehow still picks up the editable source.
    """
    venv_dir = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        check=True,
        timeout=60,
    )
    if sys.platform == "win32":
        venv_py = venv_dir / "Scripts" / "python.exe"
    else:
        venv_py = venv_dir / "bin" / "python"
    assert venv_py.is_file(), f"venv python not found at {venv_py}"

    subprocess.run(
        [str(venv_py), "-m", "pip", "install", str(built_artifacts["wheel"])],
        check=True,
        timeout=180,
        capture_output=True,
    )

    env = {
        **os.environ,
        "PATH": (f"{ncp_binary.parent}{os.pathsep}{os.environ.get('PATH', '')}"),
    }
    env.pop("PYTHONPATH", None)

    # Precondition: verify the venv's ncp_runtime import comes
    # from within venv_dir, not from the editable source tree.
    # Any import-path contamination (stray PYTHONPATH, sitecustomize,
    # ambient install) surfaces here instead of silently making the
    # demo subprocess run against the wrong code.
    import_check = subprocess.run(
        [
            str(venv_py),
            "-c",
            "import ncp_runtime; print(ncp_runtime.__file__)",
        ],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    installed_init = Path(import_check.stdout.strip())
    try:
        installed_init.resolve().relative_to(venv_dir.resolve())
    except ValueError:
        pytest.fail(
            f"ncp_runtime imported from {installed_init}, "
            f"which is NOT inside the venv at {venv_dir}. "
            f"Source-tree contamination likely; the installed-wheel "
            f"test is not actually running against the wheel install."
        )

    result = subprocess.run(
        [str(venv_py), "-m", "ncp_runtime.demo"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"demo from fresh venv exited {result.returncode};\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "demo: result_type: Success" in result.stdout
    assert "{'message': 'hello'}" in result.stdout
