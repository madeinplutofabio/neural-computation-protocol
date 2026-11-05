# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Workflow packager - produce a self-contained .tar.gz from a graph + brick-dir.

This module only VERIFIES already-built artifacts against their committed
manifests; it never recomputes digests, regenerates manifests, or rewrites
any source-of-truth field.

Uses the resolver's default `<brick_dir>/<short_name>/manifest.yaml` layout.
Graphs declaring a top-level `brick_map` field are rejected. Short-name
collisions between distinct brick_ids are rejected. Each node's
`brick.version_or_range` is validated against the referenced manifest's
`version` field. All artifact / schema paths inside a manifest are
confined to the brick's own directory (no absolute paths, no `..`
segments, no symlinks).

Version support in this release is STABLE semver only: MAJOR.MINOR.PATCH,
no prerelease or build-metadata suffix. Supported comparators are
=, >=, <=, >, <, ^ (each clause must spell its operator; no implicit =).
Prerelease handling is intentionally deferred rather than approximated.

WASM selection mirrors runtime/src/resolver.rs:
  1. artifact.path if present
  2. exactly one .wasm in the brick directory
  3. <short_name>.wasm if present among multiple candidates
  4. reject ambiguous

The Rust resolver is NOT cross-invoked. We parse YAML/JSON and compute
sha256 in Python. Rust retains execution authority at run time.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from ncp_runtime.types import NCPWorkflowDigestError, NCPWorkflowError

WORKFLOW_SCHEMA_VERSION = "ncp-workflow-0.1"

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9.+_-]{1,64}$")
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_CONSTRAINT_CLAUSE_RE = re.compile(r"^(=|>=|<=|>|<|\^)\s*(\S+)$")


def _load_yaml(p: Path) -> Any:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as e:
        raise NCPWorkflowError(
            "pyyaml is required for workflow packaging. "
            "Install it with: pip install ncp-runtime[workflows]"
        ) from e
    try:
        with open(p, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise NCPWorkflowError(f"invalid YAML at {p}: {e}") from e


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_semver(s: Any) -> tuple[int, int, int]:
    """Parse strict MAJOR.MINOR.PATCH.

    Rejects prerelease and build-metadata suffixes. The D1 packager
    supports only stable semver, matching a documented subset of Rust's
    `semver::Version` behaviour.
    """
    if not isinstance(s, str):
        raise NCPWorkflowError(f"version is not a string: {s!r}")
    stripped = s.strip()
    if not _SEMVER_RE.match(stripped):
        raise NCPWorkflowError(
            f"version {s!r} is not a stable three-part semver; the packager "
            f"rejects prerelease (-alpha, -rc.1) and build-metadata (+build) "
            f"versions in this release"
        )
    parts = stripped.split(".")
    return (int(parts[0]), int(parts[1]), int(parts[2]))


def _check_constraint(constraint: Any, actual_version: str) -> bool:
    """Supported comparators: =, >=, <=, >, <, ^.

    Every clause must spell its operator; there is no implicit =. Empty
    constraints are rejected. Prerelease and metadata versions are
    rejected (through _parse_semver).
    """
    if not isinstance(constraint, str) or not constraint.strip():
        raise NCPWorkflowError("empty version_or_range constraint")
    clauses = [c for c in re.split(r"[,\s]+", constraint.strip()) if c]
    if not clauses:
        raise NCPWorkflowError(f"empty version_or_range constraint: {constraint!r}")
    actual = _parse_semver(actual_version)
    for clause in clauses:
        m = _CONSTRAINT_CLAUSE_RE.match(clause)
        if not m:
            raise NCPWorkflowError(
                f"unparseable version clause {clause!r} in constraint "
                f"{constraint!r}; supported comparators are =, >=, <=, >, <, ^"
            )
        op, req_s = m.group(1), m.group(2)
        req = _parse_semver(req_s)
        if op == "=":
            if actual != req:
                return False
        elif op == ">=":
            if actual < req:
                return False
        elif op == ">":
            if actual <= req:
                return False
        elif op == "<=":
            if actual > req:
                return False
        elif op == "<":
            if actual >= req:
                return False
        elif op == "^":
            if actual < req:
                return False
            if req[0] > 0:
                if actual[0] >= req[0] + 1:
                    return False
            elif req[1] > 0:
                if actual[0] > 0 or actual[1] >= req[1] + 1:
                    return False
            else:
                if actual != req:
                    return False
    return True


def _safe_join(base: Path, rel_str: str, *, context: str) -> Path:
    """Resolve `base / rel_str`, confirming the target stays inside `base`.

    Rejects absolute paths, `..` segments, and symlinks encountered while
    descending to the target.
    """
    if not isinstance(rel_str, str) or rel_str == "":
        raise NCPWorkflowError(f"empty path in {context}")
    rel_path = Path(rel_str)
    if rel_path.is_absolute():
        raise NCPWorkflowError(f"absolute path not allowed in {context}: {rel_str!r}")
    if any(part == ".." for part in rel_path.parts):
        raise NCPWorkflowError(f"parent-traversal not allowed in {context}: {rel_str!r}")
    target = base / rel_path

    base_resolved = base.resolve(strict=True)
    acc = base
    for part in rel_path.parts:
        acc = acc / part
        if acc.is_symlink():
            raise NCPWorkflowError(f"symlink not allowed in {context}: {acc}")
        if not acc.exists():
            break
    resolved = target.resolve(strict=False)
    try:
        resolved.relative_to(base_resolved)
    except ValueError as e:
        raise NCPWorkflowError(
            f"path escapes brick directory in {context}: "
            f"{rel_str!r} resolves to {resolved}, outside {base_resolved}"
        ) from e
    return target


def _short_name(brick_id: str) -> str:
    return brick_id.rsplit(".", 1)[-1]


def _select_wasm(bdir: Path, sn: str, brick_id: str, artifact_path: Any) -> tuple[Path, str]:
    """Mirror runtime/src/resolver.rs WASM selection order."""
    if artifact_path:
        wasm_path = _safe_join(bdir, artifact_path, context=f"artifact.path for brick {brick_id!r}")
        if not wasm_path.is_file():
            raise NCPWorkflowError(f"wasm file not found for {brick_id!r}: {wasm_path}")
        return wasm_path, artifact_path

    wasm_entries = [p for p in bdir.iterdir() if p.suffix == ".wasm"]
    for p in wasm_entries:
        if p.is_symlink():
            raise NCPWorkflowError(f"wasm file is a symlink (not allowed): {p}")
    candidates = sorted(p for p in wasm_entries if p.is_file())
    if len(candidates) == 1:
        return candidates[0], candidates[0].name
    if len(candidates) > 1:
        fallback = bdir / f"{sn}.wasm"
        if fallback.is_file() and not fallback.is_symlink():
            return fallback, fallback.name
        raise NCPWorkflowError(
            f"ambiguous WASM selection in {bdir}: found "
            f"{[p.name for p in candidates]} and none is {sn!r}.wasm; "
            f"set artifact.path in the manifest to disambiguate"
        )
    raise NCPWorkflowError(f"no .wasm file found in {bdir} for brick {brick_id!r}")


def _validate_brick(brick_dir: Path, brick_id: str) -> dict[str, Any]:
    sn = _short_name(brick_id)
    bdir = brick_dir / sn
    if not bdir.is_dir():
        raise NCPWorkflowError(f"brick directory not found for {brick_id!r}: {bdir}")
    if bdir.is_symlink():
        raise NCPWorkflowError(f"brick directory is a symlink (not allowed): {bdir}")

    manifest_path = bdir / "manifest.yaml"
    if not manifest_path.is_file():
        raise NCPWorkflowError(f"manifest.yaml not found for {brick_id!r}: {manifest_path}")
    if manifest_path.is_symlink():
        raise NCPWorkflowError(f"manifest.yaml is a symlink (not allowed): {manifest_path}")

    m = _load_yaml(manifest_path)
    if not isinstance(m, dict):
        raise NCPWorkflowError(f"manifest {manifest_path} is not a mapping")
    if m.get("brick_id") != brick_id:
        raise NCPWorkflowError(
            f"manifest brick_id mismatch at {manifest_path}: "
            f"expected {brick_id!r}, got {m.get('brick_id')!r}"
        )

    version = m.get("version")
    if not isinstance(version, str):
        raise NCPWorkflowError(
            f"manifest {manifest_path} missing required 'version' field (string)"
        )
    _parse_semver(version)

    artifact = m.get("artifact") or {}
    if not isinstance(artifact, dict):
        raise NCPWorkflowError(f"manifest {manifest_path} 'artifact' is not a mapping")
    expected_digest = artifact.get("digest", "")
    expected_size = artifact.get("size_bytes")
    if not expected_digest or expected_size is None:
        raise NCPWorkflowError(
            f"manifest {manifest_path} missing artifact.digest or artifact.size_bytes"
        )

    wasm_path, wasm_rel = _select_wasm(bdir, sn, brick_id, artifact.get("path"))
    if wasm_path.is_symlink():
        raise NCPWorkflowError(f"wasm file is a symlink (not allowed): {wasm_path}")

    actual_size = wasm_path.stat().st_size
    if actual_size != expected_size:
        raise NCPWorkflowDigestError(
            f"artifact size mismatch for {wasm_path}: "
            f"manifest says {expected_size} bytes, on-disk {actual_size} bytes"
        )
    actual_digest = f"sha256:{_sha256_file(wasm_path)}"
    if actual_digest != expected_digest:
        raise NCPWorkflowDigestError(
            f"artifact digest mismatch for {wasm_path}: "
            f"manifest {expected_digest}, on-disk {actual_digest}"
        )

    schemas = m.get("schemas") or {}
    if not isinstance(schemas, dict):
        raise NCPWorkflowError(f"manifest {manifest_path} 'schemas' is not a mapping")
    schema_files: dict[str, tuple[str, Path]] = {}
    for key, rel in schemas.items():
        if rel is None:
            continue
        if not isinstance(rel, str):
            raise NCPWorkflowError(
                f"schema {key!r} for brick {brick_id!r} is not a string path: {rel!r}"
            )
        p = _safe_join(bdir, rel, context=f"schemas.{key} for brick {brick_id!r}")
        if not p.is_file():
            raise NCPWorkflowError(f"schema not found for {brick_id!r} ({key}): {p}")
        if p.is_symlink():
            raise NCPWorkflowError(
                f"schema is a symlink (not allowed) for {brick_id!r} ({key}): {p}"
            )
        schema_files[key] = (rel, p)

    return {
        "brick_id": brick_id,
        "short_name": sn,
        "manifest_version": version,
        "manifest_path": manifest_path,
        "wasm_path": wasm_path,
        "wasm_rel": wasm_rel,
        "schema_files": schema_files,
        "digest": expected_digest,
        "size": expected_size,
    }


def _collect_brick_refs(graph_obj: Any, graph_path: Path) -> list[dict[str, str]]:
    if not isinstance(graph_obj, dict):
        raise NCPWorkflowError(f"graph {graph_path} root is not a mapping")
    if "brick_map" in graph_obj:
        raise NCPWorkflowError(
            f"graph {graph_path} has a top-level 'brick_map' field; "
            f"the packager only supports the default "
            f"<brick_dir>/<short_name>/manifest.yaml layout"
        )
    nodes = graph_obj.get("nodes") or []
    if not nodes:
        raise NCPWorkflowError(f"graph {graph_path} has no nodes")
    refs: list[dict[str, str]] = []
    for node in nodes:
        if not isinstance(node, dict):
            raise NCPWorkflowError(f"graph {graph_path} has a non-mapping node")
        brick = node.get("brick") or {}
        if not isinstance(brick, dict):
            raise NCPWorkflowError(
                f"graph {graph_path} node {node.get('node_id')!r} 'brick' is not a mapping"
            )
        bid = brick.get("brick_id")
        vor = brick.get("version_or_range")
        if not bid:
            raise NCPWorkflowError(
                f"node {node.get('node_id')!r} in {graph_path} is missing brick.brick_id"
            )
        if not vor:
            raise NCPWorkflowError(
                f"node {node.get('node_id')!r} in {graph_path} is missing brick.version_or_range"
            )
        refs.append(
            {
                "node_id": node.get("node_id", "<unknown>"),
                "brick_id": bid,
                "version_or_range": vor,
            }
        )
    return refs


def _check_short_name_collisions(refs: list[dict[str, str]]) -> None:
    seen: dict[str, str] = {}
    for ref in refs:
        bid = ref["brick_id"]
        sn = _short_name(bid)
        if sn in seen and seen[sn] != bid:
            raise NCPWorkflowError(
                f"short_name collision: brick_ids {seen[sn]!r} and {bid!r} "
                f"both resolve to {sn!r}; the resolver cannot distinguish them"
            )
        seen[sn] = bid


def package(
    *,
    graph: str | Path,
    brick_dir: str | Path,
    name: str,
    version: str,
    runtime_compatibility: str,
    out: str | Path,
    description: str = "",
) -> dict[str, Any]:
    """Pack a graph + its bricks into a self-contained .tar.gz archive.

    Verifies every referenced brick against its committed manifest
    (digest, size, schemas, declared version satisfies each node's
    constraint). Never recomputes digests. The output archive is
    written to a per-call unique temporary sibling and atomically
    renamed; a prior archive at `out` is preserved if packaging fails.

    Returns a summary dict with keys: path, size_bytes, sha256, name,
    version, bricks.
    """
    if not _NAME_RE.match(name):
        raise NCPWorkflowError(f"invalid workflow name {name!r}; must match {_NAME_RE.pattern}")
    if not _VERSION_RE.match(version):
        raise NCPWorkflowError(
            f"invalid workflow version {version!r}; must match {_VERSION_RE.pattern}"
        )

    graph_path = Path(graph).resolve()
    brick_dir_path = Path(brick_dir).resolve()
    out_path = Path(out).resolve()

    if not graph_path.is_file():
        raise NCPWorkflowError(f"graph file not found: {graph_path}")
    if not brick_dir_path.is_dir():
        raise NCPWorkflowError(f"brick_dir not found: {brick_dir_path}")

    graph_obj = _load_yaml(graph_path)
    refs = _collect_brick_refs(graph_obj, graph_path)
    _check_short_name_collisions(refs)

    validated: dict[str, dict[str, Any]] = {}
    for ref in refs:
        bid = ref["brick_id"]
        if bid not in validated:
            validated[bid] = _validate_brick(brick_dir_path, bid)

    for ref in refs:
        info = validated[ref["brick_id"]]
        actual = info["manifest_version"]
        constraint = ref["version_or_range"]
        if not _check_constraint(constraint, actual):
            raise NCPWorkflowError(
                f"version mismatch: node {ref['node_id']!r} requires "
                f"{ref['brick_id']!r} {constraint!r}, but manifest at "
                f"{info['manifest_path']} declares version {actual!r}"
            )

    workflow_json = {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "name": name,
        "version": version,
        "description": description,
        "runtime_compatibility": runtime_compatibility,
        "graph": "graph.yaml",
        "bricks_dir": "bricks",
    }

    root_name = f"{name}-{version}"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = out_path.with_name(f".{out_path.name}.{secrets.token_hex(8)}.tmp")

    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / root_name
            root.mkdir()
            (root / "workflow.json").write_text(
                json.dumps(workflow_json, indent=2) + "\n", encoding="utf-8"
            )
            shutil.copy2(graph_path, root / "graph.yaml")

            bricks_root = root / "bricks"
            bricks_root.mkdir()
            for _bid, info in validated.items():
                dest = bricks_root / info["short_name"]
                dest.mkdir()
                shutil.copy2(info["manifest_path"], dest / "manifest.yaml")
                wasm_out = dest / info["wasm_rel"]
                wasm_out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(info["wasm_path"], wasm_out)
                for _key, (rel, p) in info["schema_files"].items():
                    schema_out = dest / rel
                    schema_out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, schema_out)

            with tarfile.open(tmp_out, "w:gz") as tf:
                for p in sorted(root.rglob("*")):
                    arcname = f"{root_name}/{p.relative_to(root).as_posix()}"
                    tf.add(p, arcname=arcname, recursive=False)

        os.replace(tmp_out, out_path)
    except BaseException:
        try:
            tmp_out.unlink()
        except FileNotFoundError:
            pass
        raise

    archive_sha = _sha256_file(out_path)
    archive_size = out_path.stat().st_size

    return {
        "path": out_path,
        "size_bytes": archive_size,
        "sha256": archive_sha,
        "name": name,
        "version": version,
        "bricks": [
            {"brick_id": bid, "digest": info["digest"], "size_bytes": info["size"]}
            for bid, info in validated.items()
        ],
    }
