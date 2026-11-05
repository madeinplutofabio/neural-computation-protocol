# NCP workflow consumption & distribution

This directory holds the worked example and CI fixture for the
workflow-consumption mechanism. The mechanism makes an NCP computation
(graph + referenced bricks + manifests) portable and installable *by
name and version*, from Python, the MCP server, and the LangGraph adapter,
without a repo clone, a Rust toolchain, or any manual YAML/WASM wrangling.

Four moving parts, all Python, all in `python/ncp-runtime/`:

| Verb              | What it does                                                  |
|-------------------|---------------------------------------------------------------|
| `package`         | Produce a self-contained `<name>-<version>.tar.gz` archive    |
| `install`         | Fetch + verify sha256 + safe-extract + validate + publish     |
| `list / show / resolve / remove` | Enumerate and manage installed workflows       |
| `mcp-config`      | Emit the exact MCP host-config for an installed workflow      |

The MCP server (`ncp-mcp-server`) and the LangGraph adapter
(`ncp-langgraph`) are **not modified** by this mechanism — they already
accept `--graph` + `--brick-dir` and `NCPNode.from_subprocess(graph=,
brick_dir=)` respectively. `mcp-config` just emits the right args, and
`workflows.resolve()` just returns the right paths.

---

## Producer walkthrough — publish your own workflow

A developer has written their own graph. The brick artifacts they
reference already exist and their manifests already contain
committed artifact digests (brick-author tooling is a separate
concern, out of scope here). They run the SDK's packager against
their graph + brick-dir:

```bash
pip install 'ncp-runtime[workflows]'

python -m ncp_runtime.workflows package \
  --graph        ./my-graph.yaml \
  --brick-dir    ./bricks \
  --name         com.my-vendor.my-workflow \
  --version      0.1.0 \
  --runtime-compat ">=0.4.0,<0.5.0" \
  --out          my-workflow-0.1.0.tar.gz
```

The packager prints the archive's sha256. The author publishes archive
and sha256 anywhere — a GitHub release, an S3 bucket, a static file host.
No registry, no account, no central service.

### What the packager verifies before writing the archive

- Each node's `version_or_range` constraint is satisfied by its brick
  manifest's `version` (stable semver only; prerelease / build-metadata
  rejected).
- Each brick's committed `artifact.digest` and `size_bytes` match the
  on-disk WASM, byte for byte. Digests are never recomputed or rewritten.
- Artifact and schema paths inside every manifest stay within the brick's
  own directory (no absolute paths, no `..`, no symlinks).
- WASM selection follows the runtime's rule: `artifact.path` if present,
  else the single `.wasm` in the brick dir, else `<short_name>.wasm` as
  the fallback; ambiguous layouts are rejected.
- Short-name collisions between two distinct `brick_id`s are rejected.
- Top-level `brick_map` layouts are rejected (this packager supports
  only the default `<brick_dir>/<short_name>/manifest.yaml` layout).
- The output archive is written atomically: a unique `.tmp` sibling is
  renamed over the final path only after all files are in place, so a
  prior archive at `--out` is preserved if packaging fails.

---

## Consumer walkthrough — install someone else's workflow

In a **clean virtual env**, with **no repo clone** and **no Rust toolchain**:

```bash
pip install 'ncp-runtime[workflows]'

python -m ncp_runtime.workflows install \
  https://example.org/my-workflow-0.1.0.tar.gz \
  --sha256 EXPECTED_ARCHIVE_SHA256
```

Both a local path and a `file://` URL are also accepted. Everything else
(`http://`, `ssh://`, `git+*`, …) is explicitly rejected.

### What the installer enforces

- Supported sources only: local filesystem path, `file://`, `https://`.
  No implicit network calls to unknown hosts.
- The `--sha256` argument is verified **before extraction**. HTTPS
  downloads are streamed with a size cap (`NCP_WORKFLOW_MAX_SIZE`,
  default 100 MiB) and redirects to any non-https scheme are refused.
- Safe extraction: no absolute paths, no `..` segments, no symlinks or
  hardlinks, no duplicates, no case-insensitive collisions, no
  Windows-reserved segments (`AUX`, `CON`, …), no control characters,
  no trailing `.` or ` `.
- `workflow.json` strict-validated against the `ncp-workflow-0.1` schema.
- The resolved runtime binary's version must satisfy
  `workflow.runtime_compatibility` — rejection is loud, not a warning.
- Every brick's manifest, schemas, artifact digest and size are
  re-validated against on-disk content, using the same validators the
  packager used. (The Rust resolver is not cross-invoked; Rust retains
  execution authority at run time.)
- Publish is atomic: the fully-populated staging sibling is renamed into
  the final `<home>/<name>/<version>/` slot in a single filesystem
  operation. An interrupted install never leaves a zombie install dir;
  a stale staging sibling is reaped on the next install retry. The
  `(name, version)` pair is immutable — no `--force` replace.

### Where it lands

`~/.ncp/workflows/<name>/<version>/` by default; `NCP_WORKFLOW_HOME`
overrides the root. Side-by-side versions are the norm — installing
`0.1.0` and `0.2.0` of the same name leaves both on disk, each
addressable explicitly.

---

## Running an installed workflow

Three hosts, one installed workflow:

### Python SDK

```python
from ncp_runtime import NCP

result = NCP().run_workflow(
    "com.my-vendor.my-workflow",
    inputs={"text": "hello"},
    version="0.1.0",      # optional when only one version is installed
)
assert result.result_type == "Success"
print(result.terminals[0].output)
```

`NCP.run_workflow()` delegates to the existing `NCP.run()` — the `Result`
contract is unchanged.

### MCP

```bash
python -m ncp_runtime.workflows mcp-config \
    com.my-vendor.my-workflow --version 0.1.0 --format snippet
```

Prints a copy-pasteable `mcpServers` block for Claude Desktop, Cursor,
Continue, or any other MCP host that consumes stdio-server configs.
The `ncp-mcp-server` binary itself is unchanged.

### LangGraph

```python
from ncp_runtime.workflows import resolve
from ncp_langgraph import NCPNode

graph_path, brick_dir = resolve(
    "com.my-vendor.my-workflow", version="0.1.0"
)
node = NCPNode.from_subprocess(
    graph=graph_path,
    brick_dir=brick_dir,
    binary="ncp-mcp-server",
)
```

The `ncp-langgraph` adapter is unchanged — the installed-workflow
resolver just supplies the `(graph, brick_dir)` pair that
`NCPNode.from_subprocess` already accepts.

---

## Managing installs

```bash
python -m ncp_runtime.workflows list
python -m ncp_runtime.workflows show com.my-vendor.my-workflow --version 0.1.0
python -m ncp_runtime.workflows resolve com.my-vendor.my-workflow
python -m ncp_runtime.workflows remove com.my-vendor.my-workflow --version 0.1.0
python -m ncp_runtime.workflows remove com.my-vendor.my-workflow --all
```

An updated workflow is just a new archive with a new `version` field in
its `workflow.json`. Consumers install the new archive; they never
rebuild WASM, never edit a digest, never touch the old one until they
choose to remove it.

---

## Scope — what this mechanism does NOT provide

- No central registry, no account system, no publisher-trust claim
  (hash match proves integrity against the publisher's supplied digest,
  not that the publisher's workflow is safe to run — artifact trust
  roots are Phase 3C).
- No automatic "latest" resolution; explicit versions only.
- No background update polling.
- No cloud distribution service — distribution is file-based.
- `pip install ncp-runtime` installs the SDK + bundled runtime binary;
  it does NOT install the MCP server (crates.io) or the LangGraph
  adapter (PyPI). Install those separately when you need them.

---

## `_test-generic/` — the CI fixture

`examples/workflows/_test-generic/graph.yaml` is a single-brick echo
workflow with a deliberately generic name (`example.generic-echo`), used
by the `test_workflows_generic_producer_consumer.py` integration test to
prove the installer has no name-specific code path for the shipped NCP
examples.
