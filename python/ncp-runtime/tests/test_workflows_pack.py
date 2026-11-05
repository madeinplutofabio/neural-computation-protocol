# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Delivery 1 tests: workflow packager."""

from __future__ import annotations

import hashlib
import json
import os
import re as _re
import shutil
import sys
import tarfile
from pathlib import Path

import pytest

pytest.importorskip("yaml", reason="pyyaml required for workflow packaging tests")

from ncp_runtime.types import (  # noqa: E402
    NCPBinaryNotFoundError,
    NCPWorkflowDigestError,
    NCPWorkflowError,
)
from ncp_runtime.workflows.pack import WORKFLOW_SCHEMA_VERSION, package  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
LEAD_QUAL_GRAPH = REPO_ROOT / "examples" / "graphs" / "lead-qualification" / "graph.yaml"
LEAD_QUAL_SAMPLES = REPO_ROOT / "examples" / "graphs" / "lead-qualification" / "samples"
BRICKS_DIR = REPO_ROOT / "examples" / "bricks"

# A placeholder sha256 used for graph_version / synaptic_state_version in
# synthetic test graphs; the packager does not validate these fields.
_DUMMY_SHA = "sha256:" + "0" * 63 + "1"


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_graph(path: Path, graph_id: str, nodes_yaml: str) -> None:
    path.write_text(
        f"graph_id: {graph_id}\n"
        f'graph_version: "{_DUMMY_SHA}"\n'
        f'synaptic_state_version: "{_DUMMY_SHA}"\n'
        f"nodes:\n{nodes_yaml}\n",
        encoding="utf-8",
    )


@pytest.fixture
def out_dir(tmp_path: Path) -> Path:
    d = tmp_path / "dist"
    d.mkdir()
    return d


# -- Happy path ------------------------------------------------------------


def test_package_lead_qualification_round_trip(out_dir: Path) -> None:
    out = out_dir / "lead-qualification-0.1.0.tar.gz"
    result = package(
        graph=LEAD_QUAL_GRAPH,
        brick_dir=BRICKS_DIR,
        name="org.ncp-examples.lead-qualification",
        version="0.1.0",
        runtime_compatibility=">=0.3.6,<0.4.0",
        description="test package",
        out=out,
    )
    assert out.is_file()
    assert result["sha256"] == _sha256_file(out)
    assert result["size_bytes"] == out.stat().st_size
    assert len(result["bricks"]) == 2

    extract = out_dir / "extract"
    extract.mkdir()
    with tarfile.open(out, "r:gz") as tf:
        tf.extractall(extract)
    root = extract / "org.ncp-examples.lead-qualification-0.1.0"
    wf = json.loads((root / "workflow.json").read_text(encoding="utf-8"))
    assert wf["schema_version"] == WORKFLOW_SCHEMA_VERSION
    assert (root / "graph.yaml").is_file()
    assert (root / "bricks" / "company-profile-lookup" / "manifest.yaml").is_file()
    assert (root / "bricks" / "company-profile-lookup" / "company-profile-lookup.wasm").is_file()
    assert (root / "bricks" / "icp-qualifier" / "icp-qualifier.wasm").is_file()


# -- Runtime execution (required in CI via NCP_REQUIRE_RUNTIME=1) ---------


def test_extracted_archive_executes_on_real_runtime(out_dir: Path) -> None:
    """Pack the lead-qualification workflow, extract to a fresh location,
    and prove the extracted archive yields byte-identical business output
    to the original graph through the real NCP runtime.

    Required in the CI job that sets NCP_REQUIRE_RUNTIME=1.
    """
    from ncp_runtime import NCP

    sample_path = LEAD_QUAL_SAMPLES / "qualified_2_of_3.json"
    if not sample_path.is_file():
        samples = sorted(LEAD_QUAL_SAMPLES.glob("*.json"))
        if not samples:
            pytest.skip("no sample inputs available for lead-qualification")
        sample_path = samples[0]
    inputs = json.loads(sample_path.read_text(encoding="utf-8"))

    # Honor NCP_RUNTIME explicitly: discovery can resolve to a stale
    # binary on PATH (the ~/.cargo/bin/ncp.exe precedence issue flagged
    # as out of scope for this PR). Passing `binary=` bypasses that.
    bin_env = os.environ.get("NCP_RUNTIME")
    try:
        if bin_env:
            ncp = NCP(binary=Path(bin_env), timeout=20.0)
        else:
            ncp = NCP(timeout=20.0)
        baseline = ncp.run(graph=LEAD_QUAL_GRAPH, brick_dir=BRICKS_DIR, inputs=inputs)
    except NCPBinaryNotFoundError:
        if os.environ.get("NCP_REQUIRE_RUNTIME"):
            raise
        pytest.skip("ncp runtime binary not available; set NCP_REQUIRE_RUNTIME=1 to require")

    assert baseline.result_type == "Success", f"baseline run failed: {baseline!r}"
    assert baseline.terminals, "baseline produced no terminal"
    baseline_output = baseline.terminals[0].output
    assert isinstance(baseline_output, dict), "baseline output must be a dict"

    out = out_dir / "lead-qualification-0.1.0.tar.gz"
    package(
        graph=LEAD_QUAL_GRAPH,
        brick_dir=BRICKS_DIR,
        name="org.ncp-examples.lead-qualification",
        version="0.1.0",
        runtime_compatibility=">=0.3.6,<0.4.0",
        out=out,
    )
    extract = out_dir / "extracted"
    extract.mkdir()
    with tarfile.open(out, "r:gz") as tf:
        tf.extractall(extract)
    root = extract / "org.ncp-examples.lead-qualification-0.1.0"

    extracted = ncp.run(graph=root / "graph.yaml", brick_dir=root / "bricks", inputs=inputs)
    assert extracted.result_type == "Success", f"extracted-archive run failed: {extracted!r}"
    assert extracted.terminals, "extracted archive produced no terminal"
    assert extracted.terminals[0].output == baseline_output, (
        "extracted archive must produce byte-identical business output to the "
        f"original graph. baseline={baseline_output!r} "
        f"extracted={extracted.terminals[0].output!r}"
    )


# -- Input validation -----------------------------------------------------


def test_package_rejects_missing_graph(out_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(NCPWorkflowError, match="graph file not found"):
        package(
            graph=tmp_path / "nonexistent.yaml",
            brick_dir=BRICKS_DIR,
            name="org.test.x",
            version="0.1.0",
            runtime_compatibility=">=0.3.6,<0.4.0",
            out=out_dir / "x.tar.gz",
        )


def test_package_rejects_missing_brick_dir(out_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(NCPWorkflowError, match="brick_dir not found"):
        package(
            graph=LEAD_QUAL_GRAPH,
            brick_dir=tmp_path / "nonexistent",
            name="org.test.x",
            version="0.1.0",
            runtime_compatibility=">=0.3.6,<0.4.0",
            out=out_dir / "x.tar.gz",
        )


def test_package_rejects_brick_map(tmp_path: Path, out_dir: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "graph_id: org.test.x\n"
        f'graph_version: "{_DUMMY_SHA}"\n'
        f'synaptic_state_version: "{_DUMMY_SHA}"\n'
        "brick_map: ./bricks.yaml\n"
        "nodes:\n"
        "  - node_id: n\n"
        '    brick: { brick_id: org.ncp-examples.echo, version_or_range: "=0.1.0" }\n',
        encoding="utf-8",
    )
    with pytest.raises(NCPWorkflowError, match="brick_map"):
        package(
            graph=bad,
            brick_dir=BRICKS_DIR,
            name="org.test.x",
            version="0.1.0",
            runtime_compatibility=">=0.3.6,<0.4.0",
            out=out_dir / "x.tar.gz",
        )


def test_package_rejects_short_name_collision(tmp_path: Path, out_dir: Path) -> None:
    bad = tmp_path / "collide.yaml"
    _write_graph(
        bad,
        "org.test.collision",
        "  - node_id: a\n"
        '    brick: { brick_id: org.ncp-examples.echo, version_or_range: "=0.1.0" }\n'
        "  - node_id: b\n"
        '    brick: { brick_id: org.other-vendor.echo, version_or_range: "=0.1.0" }',
    )
    with pytest.raises(NCPWorkflowError, match="short_name collision"):
        package(
            graph=bad,
            brick_dir=BRICKS_DIR,
            name="org.test.collision",
            version="0.1.0",
            runtime_compatibility=">=0.3.6,<0.4.0",
            out=out_dir / "x.tar.gz",
        )


# -- Integrity -----------------------------------------------------------


def test_package_rejects_digest_mismatch(tmp_path: Path, out_dir: Path) -> None:
    staged = tmp_path / "bricks"
    shutil.copytree(BRICKS_DIR / "icp-qualifier", staged / "icp-qualifier")
    shutil.copytree(BRICKS_DIR / "company-profile-lookup", staged / "company-profile-lookup")
    wasm = staged / "icp-qualifier" / "icp-qualifier.wasm"
    data = bytearray(wasm.read_bytes())
    data[1000] ^= 0x01
    wasm.write_bytes(bytes(data))
    # The typed subclass is part of the contract: integrity failures must
    # be NCPWorkflowDigestError, not just the base NCPWorkflowError.
    with pytest.raises(NCPWorkflowDigestError, match="digest mismatch|size mismatch"):
        package(
            graph=LEAD_QUAL_GRAPH,
            brick_dir=staged,
            name="org.ncp-examples.lead-qualification",
            version="0.1.0",
            runtime_compatibility=">=0.3.6,<0.4.0",
            out=out_dir / "tampered.tar.gz",
        )


# -- Version constraints --------------------------------------------------


def test_package_rejects_node_version_constraint_mismatch(tmp_path: Path, out_dir: Path) -> None:
    bad = tmp_path / "bad-version.yaml"
    _write_graph(
        bad,
        "org.test.bad-version",
        "  - node_id: n\n"
        '    brick: { brick_id: org.ncp-examples.echo, version_or_range: "=9.9.9" }',
    )
    with pytest.raises(NCPWorkflowError, match="version mismatch"):
        package(
            graph=bad,
            brick_dir=BRICKS_DIR,
            name="org.test.bad-version",
            version="0.1.0",
            runtime_compatibility=">=0.3.6,<0.4.0",
            out=out_dir / "x.tar.gz",
        )


def test_package_rejects_unsupported_version_comparator(tmp_path: Path, out_dir: Path) -> None:
    bad = tmp_path / "bad-syntax.yaml"
    _write_graph(
        bad,
        "org.test.bad-syntax",
        "  - node_id: n\n"
        '    brick: { brick_id: org.ncp-examples.echo, version_or_range: "~0.1.0" }',
    )
    with pytest.raises(
        NCPWorkflowError,
        match="unparseable version clause|not a stable three-part semver",
    ):
        package(
            graph=bad,
            brick_dir=BRICKS_DIR,
            name="org.test.bad-syntax",
            version="0.1.0",
            runtime_compatibility=">=0.3.6,<0.4.0",
            out=out_dir / "x.tar.gz",
        )


def test_package_rejects_prerelease_constraint(tmp_path: Path, out_dir: Path) -> None:
    bad = tmp_path / "prerelease.yaml"
    _write_graph(
        bad,
        "org.test.prerelease",
        "  - node_id: n\n"
        '    brick: { brick_id: org.ncp-examples.echo, version_or_range: "=0.1.0-alpha" }',
    )
    with pytest.raises(NCPWorkflowError, match="not a stable three-part semver"):
        package(
            graph=bad,
            brick_dir=BRICKS_DIR,
            name="org.test.prerelease",
            version="0.1.0",
            runtime_compatibility=">=0.3.6,<0.4.0",
            out=out_dir / "x.tar.gz",
        )


# -- Path confinement ----------------------------------------------------


def _stage_echo(base: Path) -> Path:
    staged = base / "bricks"
    shutil.copytree(BRICKS_DIR / "echo", staged / "echo")
    return staged


def _patch_manifest(
    mf: Path,
    *,
    new_artifact_path: str | None = None,
    extra_schema: tuple[str, str] | None = None,
) -> None:
    txt = mf.read_text(encoding="utf-8")
    if new_artifact_path is not None:
        if _re.search(r"^\s+path:\s+.*$", txt, flags=_re.MULTILINE):
            txt = _re.sub(
                r"(^\s+path:\s+).*$",
                lambda _m: f"{_m.group(1)}{new_artifact_path}",
                txt,
                count=1,
                flags=_re.MULTILINE,
            )
        else:
            txt = _re.sub(
                r"(^artifact:\s*$)",
                lambda _m: f"{_m.group(1)}\n  path: {new_artifact_path}",
                txt,
                count=1,
                flags=_re.MULTILINE,
            )
    if extra_schema is not None:
        key, val = extra_schema
        if _re.search(r"^schemas:\s*$", txt, flags=_re.MULTILINE):
            txt = _re.sub(
                r"(^schemas:\s*$)",
                lambda _m: f"{_m.group(1)}\n  {key}: {val}",
                txt,
                count=1,
                flags=_re.MULTILINE,
            )
        else:
            txt = txt.rstrip() + f"\nschemas:\n  {key}: {val}\n"
    mf.write_text(txt, encoding="utf-8")


def test_package_rejects_absolute_artifact_path(tmp_path: Path, out_dir: Path) -> None:
    staged = _stage_echo(tmp_path)
    mf = staged / "echo" / "manifest.yaml"
    abs_target = "C:/Windows/System32/notepad.exe" if os.name == "nt" else "/etc/passwd"
    _patch_manifest(mf, new_artifact_path=abs_target)

    graph = tmp_path / "g.yaml"
    _write_graph(
        graph,
        "org.test.abs-path",
        "  - node_id: n\n"
        '    brick: { brick_id: org.ncp-examples.echo, version_or_range: "=0.1.0" }',
    )
    with pytest.raises(NCPWorkflowError, match="absolute path not allowed"):
        package(
            graph=graph,
            brick_dir=staged,
            name="org.test.abs-path",
            version="0.1.0",
            runtime_compatibility=">=0.3.6,<0.4.0",
            out=out_dir / "x.tar.gz",
        )


def test_package_rejects_parent_traversal_schema_path(tmp_path: Path, out_dir: Path) -> None:
    staged = _stage_echo(tmp_path)
    mf = staged / "echo" / "manifest.yaml"
    _patch_manifest(mf, extra_schema=("evil", "../../../secret.json"))

    graph = tmp_path / "g.yaml"
    _write_graph(
        graph,
        "org.test.traversal",
        "  - node_id: n\n"
        '    brick: { brick_id: org.ncp-examples.echo, version_or_range: "=0.1.0" }',
    )
    with pytest.raises(NCPWorkflowError, match="parent-traversal not allowed"):
        package(
            graph=graph,
            brick_dir=staged,
            name="org.test.traversal",
            version="0.1.0",
            runtime_compatibility=">=0.3.6,<0.4.0",
            out=out_dir / "x.tar.gz",
        )


@pytest.mark.skipif(
    sys.platform == "win32" and not os.environ.get("ALLOW_SYMLINK_TESTS"),
    reason=(
        "Windows symlink creation requires Developer Mode or admin; "
        "set ALLOW_SYMLINK_TESTS=1 to run"
    ),
)
def test_package_rejects_symlink_wasm(tmp_path: Path, out_dir: Path) -> None:
    staged = _stage_echo(tmp_path)
    wasm_target = staged / "echo" / "echo.wasm"
    outside = tmp_path / "other.wasm"
    outside.write_bytes(wasm_target.read_bytes())
    wasm_target.unlink()
    os.symlink(outside, wasm_target)

    graph = tmp_path / "g.yaml"
    _write_graph(
        graph,
        "org.test.symlink",
        "  - node_id: n\n"
        '    brick: { brick_id: org.ncp-examples.echo, version_or_range: "=0.1.0" }',
    )
    with pytest.raises(NCPWorkflowError, match="symlink not allowed|is a symlink"):
        package(
            graph=graph,
            brick_dir=staged,
            name="org.test.symlink",
            version="0.1.0",
            runtime_compatibility=">=0.3.6,<0.4.0",
            out=out_dir / "x.tar.gz",
        )


# -- WASM selection parity -----------------------------------------------


def test_wasm_selection_lone_wasm_without_path_field(tmp_path: Path, out_dir: Path) -> None:
    """If manifest omits artifact.path and exactly one .wasm is present,
    the selector returns it (step 2 of the resolver's order)."""
    staged = _stage_echo(tmp_path)
    # Echo ships two .wasm files (echo.wasm + trap.wasm); drop the
    # secondary so exactly one remains.
    (staged / "echo" / "trap.wasm").unlink()

    graph = tmp_path / "g.yaml"
    _write_graph(
        graph,
        "org.test.lone-wasm",
        "  - node_id: n\n"
        '    brick: { brick_id: org.ncp-examples.echo, version_or_range: "=0.1.0" }',
    )
    out = out_dir / "lone.tar.gz"
    result = package(
        graph=graph,
        brick_dir=staged,
        name="org.test.lone-wasm",
        version="0.1.0",
        runtime_compatibility=">=0.3.6,<0.4.0",
        out=out,
    )
    assert out.is_file()
    assert len(result["bricks"]) == 1


def test_wasm_selection_ambiguous_falls_back_to_short_name(tmp_path: Path, out_dir: Path) -> None:
    """Echo ships echo.wasm + trap.wasm with no artifact.path field;
    the selector must pick echo.wasm via fallback (step 3)."""
    staged = _stage_echo(tmp_path)

    graph = tmp_path / "g.yaml"
    _write_graph(
        graph,
        "org.test.fallback",
        "  - node_id: n\n"
        '    brick: { brick_id: org.ncp-examples.echo, version_or_range: "=0.1.0" }',
    )
    out = out_dir / "fallback.tar.gz"
    result = package(
        graph=graph,
        brick_dir=staged,
        name="org.test.fallback",
        version="0.1.0",
        runtime_compatibility=">=0.3.6,<0.4.0",
        out=out,
    )
    assert out.is_file()
    assert len(result["bricks"]) == 1


def test_wasm_selection_ambiguous_without_match_rejected(tmp_path: Path, out_dir: Path) -> None:
    """Multiple .wasm, none named <short_name>.wasm, no artifact.path -> reject."""
    staged = _stage_echo(tmp_path)
    # Rename both so no .wasm matches short_name "echo".
    (staged / "echo" / "echo.wasm").rename(staged / "echo" / "primary.wasm")
    (staged / "echo" / "trap.wasm").rename(staged / "echo" / "secondary.wasm")

    graph = tmp_path / "g.yaml"
    _write_graph(
        graph,
        "org.test.ambig",
        "  - node_id: n\n"
        '    brick: { brick_id: org.ncp-examples.echo, version_or_range: "=0.1.0" }',
    )
    with pytest.raises(NCPWorkflowError, match="ambiguous WASM selection"):
        package(
            graph=graph,
            brick_dir=staged,
            name="org.test.ambig",
            version="0.1.0",
            runtime_compatibility=">=0.3.6,<0.4.0",
            out=out_dir / "x.tar.gz",
        )


# -- Atomic write --------------------------------------------------------


def test_package_preserves_prior_output_on_failure(tmp_path: Path, out_dir: Path) -> None:
    out = out_dir / "preserve.tar.gz"
    result = package(
        graph=LEAD_QUAL_GRAPH,
        brick_dir=BRICKS_DIR,
        name="org.ncp-examples.lead-qualification",
        version="0.1.0",
        runtime_compatibility=">=0.3.6,<0.4.0",
        out=out,
    )
    original_sha = result["sha256"]
    assert out.is_file()

    with pytest.raises(NCPWorkflowError):
        package(
            graph=LEAD_QUAL_GRAPH,
            brick_dir=tmp_path / "does-not-exist",
            name="org.ncp-examples.lead-qualification",
            version="0.2.0",
            runtime_compatibility=">=0.3.6,<0.4.0",
            out=out,
        )

    assert out.is_file()
    assert _sha256_file(out) == original_sha
    strays = list(out_dir.glob(".*.tmp"))
    assert not strays, f"stray tmp files: {strays}"


# -- Independent-author graph --------------------------------------------


def test_package_independent_author_graph_no_name_hardcoding(tmp_path: Path, out_dir: Path) -> None:
    author = tmp_path / "author-graph.yaml"
    _write_graph(
        author,
        "com.indie-vendor.hello",
        "  - node_id: n\n"
        '    brick: { brick_id: org.ncp-examples.echo, version_or_range: "=0.1.0" }',
    )
    out = out_dir / "com.indie-vendor.hello-0.1.0.tar.gz"
    result = package(
        graph=author,
        brick_dir=BRICKS_DIR,
        name="com.indie-vendor.hello",
        version="0.1.0",
        runtime_compatibility=">=0.3.6,<0.4.0",
        out=out,
    )
    assert out.is_file()
    assert result["name"] == "com.indie-vendor.hello"
    assert len(result["bricks"]) == 1
    assert result["bricks"][0]["brick_id"] == "org.ncp-examples.echo"
