<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# NCP starters

A **starter** is a copy-paste-able NCP workflow project. Fork one, edit the small business-logic file, run one build command, and you get a packaged, versioned, installable workflow archive.

Starters are distinct from the other `examples/` directories:

| Directory | What's in it | Who edits it |
|---|---|---|
| [`examples/bricks/`](../bricks/) | **Bricks**: compiled WebAssembly units with manifests + schemas | NCP maintainers (canonical reference artifacts with pinned digests) |
| [`examples/graphs/`](../graphs/) | **Graphs**: reference workflow graphs demonstrating NCP features | NCP maintainers |
| [`examples/workflows/`](../workflows/) | **Workflow walkthrough**: the producer + consumer tutorial for NCP 0.4.0 | NCP maintainers |
| `examples/starters/` (here) | **Starters**: templates you copy and own | You, after copying out of this repo |

## Available starters

| Name | What it does | What you edit | Toolchain | Link |
|---|---|---|---|---|
| `basic-classifier` | Keyword-based ticket classifier (billing / support / general). Single-brick, single-node workflow. | One `classify()` function in Rust | Rust 1.94+, Python 3.10+ | [basic-classifier/](basic-classifier/) |

More starters land here when they prove a distinct customization pattern, not just a different set of keywords.

## Contribution contract (for anyone adding a new starter)

Starters live in subdirectories of `examples/starters/`. If you want to add one, open a PR that follows this contract.

### Required files

Mirror the `basic-classifier/` layout:

- `README.md` with: one-sentence description, prerequisites, "Try and customize" journey, "Publish your own" (all identity fields to update, with concrete file names; use the `basic-classifier` starter as the reference), limitations (one short paragraph)
- `brick/` (standalone Rust crate with empty `[workspace]` block so `cp -r` works outside the NCP repo)
- `brick/Cargo.lock` committed, so `--locked` works on a fresh copy
- `workflow/graph.yaml`
- `workflow/bricks/<short-name>/manifest.template.yaml` (with `sha256:PLACEHOLDER` and `size_bytes: 0`)
- `workflow/bricks/<short-name>/schemas/{input,output}.schema.json`
- `samples/{valid,invalid}.json`: both positive AND negative input fixtures
- `scripts/{stamp_manifest.py,build.sh,build.ps1}`: cross-platform build pipeline that ends with `python -m ncp_runtime.workflows package`
- `tests/test_round_trip.py`: install + run_workflow + assert expected business outputs

### Naming

- Use `com.your-org.<short-name>` for the brick ID. Do NOT use `org.ncp-examples.*` (that namespace is reserved for canonical bricks maintained by the NCP project).
- Keep the brick's short name (the trailing path segment of `brick_id`) equal to the directory name under `workflow/bricks/`. Otherwise the runtime resolver won't find your WASM.

### Compatibility pin

- The `--runtime-compat` argument in `scripts/build.sh` and `scripts/build.ps1` MUST specify a published NCP runtime cohort (for example, `">=0.4.0,<0.5.0"`). See the [compatibility matrix](../../docs/COMPATIBILITY.md).

### CI validation (required)

Every new starter MUST ship its own CI validation step. The NCP `starter-validate.yml` workflow does NOT dynamically discover and run arbitrary scripts from contributed folders. Your PR must add a job (or extend an existing one) that:

- Runs on Linux, macOS, and Windows
- Uses `permissions: contents: read` and no repository secrets
- Executes YOUR starter's `scripts/build.sh` / `scripts/build.ps1`
- Runs YOUR starter's `tests/test_round_trip.py`

Review will not land a starter whose CI isn't demonstrably green on all three OSes.

## Maintenance stance

Starters merged into this directory are **NCP-maintained reference implementations**. The maintainers treat them as surface area: they get updated on coordinated NCP releases, and breaking changes require a bump.

For **community workflows** that an author wants to distribute without NCP taking maintenance responsibility, the pattern is different: publish the archive + its SHA-256 from your own repo (GitHub Release, static host, anywhere), and link to it from a future community-index page. NCP does not absorb third-party workflows into this directory by default; a link is the long-term compact.

## See also

- [Workflow packaging and consumption](../workflows/README.md): how NCP 0.4.0 workflows are built, distributed, and installed
- [Compatibility matrix](../../docs/COMPATIBILITY.md): which runtime versions each cohort supports
- [Protocol specification](../../spec/ncp-v0.2.3.md)
