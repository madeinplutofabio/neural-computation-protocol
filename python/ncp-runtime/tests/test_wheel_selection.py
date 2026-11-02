# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Wheel-selection smoke: pip picks the correct platform wheel from a
dist/ directory holding the five overlapping wheels we publish (four
platform wheels + the ``py3-none-any`` fallback).

Exercises the real ``pip`` resolver via ``pip download --platform ...``
with ``--no-index --find-links <dist>`` so the test is fully offline.

Covers the two directions required by docs/PYTHON_SDK.md §11:

  1. A supported platform tag selects its platform wheel.
  2. An unsupported platform tag falls back to ``py3-none-any``.

The platform-wheel binary itself is not loaded here (we're testing
pip's selection logic, not runtime execution). The bundled-content
assertions live in ``test_wheel_contents.py`` Section 4.
"""

from __future__ import annotations

import shutil as _shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

import retag_wheel  # noqa: E402

_PLATFORM_TAGS = [
    "manylinux_2_28_x86_64",
    "macosx_11_0_arm64",
    "macosx_10_13_x86_64",
    "win_amd64",
]


@pytest.fixture(scope="session")
def multi_platform_dist(
    built_artifacts: dict[str, Path],
    tmp_path_factory: pytest.TempPathFactory,
) -> Path:
    """Produce a dist/ dir holding the 5-wheel publish payload:
    1 fallback (``py3-none-any``, copied from the base wheel) and 4
    platform wheels (retagged from the same base).

    The base wheel built by ``built_artifacts`` is already tagged
    ``py3-none-any`` and serves as the fallback as-is.
    """
    dist = tmp_path_factory.mktemp("multi-dist")
    base = built_artifacts["wheel"]
    _shutil.copy2(base, dist)

    for tag in _PLATFORM_TAGS:
        rc = retag_wheel.main(
            [
                "--wheel",
                str(base),
                "--platform-tag",
                tag,
                "--outdir",
                str(dist),
            ]
        )
        assert rc == 0, f"retag failed for {tag}"

    wheels = sorted(dist.glob("*.whl"))
    assert len(wheels) == 5, f"expected 5 wheels in dist, got {wheels}"
    assert any(w.name.endswith("-py3-none-any.whl") for w in wheels)
    for tag in _PLATFORM_TAGS:
        assert any(w.name.endswith(f"-py3-none-{tag}.whl") for w in wheels), (
            f"missing retagged wheel for {tag}"
        )
    return dist


def _pip_download(
    platform: str,
    source_dir: Path,
    out_dir: Path,
) -> subprocess.CompletedProcess:
    out_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--no-deps",
            "--no-index",
            "--find-links",
            str(source_dir),
            "--dest",
            str(out_dir),
            "--platform",
            platform,
            "--python-version",
            "3.10",
            "--implementation",
            "py",
            "--abi",
            "none",
            "--only-binary",
            ":all:",
            "ncp-runtime",
        ],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "platform,expected_suffix",
    [
        ("manylinux_2_28_x86_64", "-manylinux_2_28_x86_64.whl"),
        ("macosx_11_0_arm64", "-macosx_11_0_arm64.whl"),
        ("macosx_10_13_x86_64", "-macosx_10_13_x86_64.whl"),
        ("win_amd64", "-win_amd64.whl"),
    ],
)
def test_pip_selects_platform_wheel_for_matching_target(
    multi_platform_dist: Path,
    tmp_path: Path,
    platform: str,
    expected_suffix: str,
) -> None:
    out = tmp_path / f"download-{platform}"
    proc = _pip_download(platform, multi_platform_dist, out)
    assert proc.returncode == 0, (
        f"pip download for --platform {platform} failed:\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    downloaded = list(out.glob("*.whl"))
    assert len(downloaded) == 1, downloaded
    assert downloaded[0].name.endswith(expected_suffix), (
        f"pip picked {downloaded[0].name!r} for --platform {platform}; "
        f"expected a wheel ending in {expected_suffix!r}"
    )


def test_pip_falls_back_to_py3_none_any_for_unsupported_arch(
    multi_platform_dist: Path,
    tmp_path: Path,
) -> None:
    """An architecture we don't publish a platform wheel for (e.g.
    linux_aarch64 or riscv64) must resolve to the universal
    py3-none-any fallback; otherwise pip would refuse to install
    on that platform."""
    out = tmp_path / "download-aarch64"
    proc = _pip_download("linux_aarch64", multi_platform_dist, out)
    assert proc.returncode == 0, (
        f"pip download for unsupported arch failed:\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    downloaded = list(out.glob("*.whl"))
    assert len(downloaded) == 1
    assert downloaded[0].name.endswith("-py3-none-any.whl"), (
        f"expected py3-none-any fallback for linux_aarch64, got {downloaded[0].name}"
    )
