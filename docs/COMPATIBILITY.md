<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# NCP compatibility and versioning policy

NCP ships as several packages. This document says which versions
work together and how they are versioned.

## 1. Coordinated release cohort (starting with NCP 0.4.0)

The four maintained implementation packages advance together as one
coordinated NCP release. A given NCP release is identified by its
coordinated version; every maintained package carries that same
version, whether it changed under the hood or was revalidated and
republished as part of the tested set.

| Package | Where | NCP 0.4.0 |
|---|---|---|
| Rust execution runtime | crates.io: [`ncp-runtime`](https://crates.io/crates/ncp-runtime) | 0.4.0 |
| Python SDK (bundles the runtime binary) | PyPI: [`ncp-runtime`](https://pypi.org/project/ncp-runtime/) | 0.4.0 |
| MCP server | crates.io: [`ncp-mcp-server`](https://crates.io/crates/ncp-mcp-server) | 0.4.0 |
| LangGraph adapter | PyPI: [`ncp-langgraph`](https://pypi.org/project/ncp-langgraph/) | 0.4.0 |

The Python SDK pins `SUPPORTED_RUNTIME_VERSIONS = ("0.4.0",)`. A
different runtime binary version fails the SDK's version-check with
`NCPRuntimeVersionError`; expanding this tuple is a deliberate SDK
change, never implicit.

## 2. What is NOT in the coordinated cohort

These advance on their own cadence and do NOT move with NCP releases:

| Artifact | Why it is independent |
|---|---|
| **NCP protocol specification** (currently [v0.2.3](../spec/ncp-v0.2.3.md)) | Normative wire-and-execution contract. It moves only when the contract itself changes, under the policy inside the spec (v0.2.x stays backward-compatible; breaking changes bump the minor version). |
| **Workflow archive format** (`workflow.json`, `schema_version: "ncp-workflow-0.1"`) | Moves only when the archive layout evolves. |
| **Brick crates** (reference bricks in `bricks/`) | A brick is a separately versioned computation. An echo brick at `0.1.0` can be compatible with NCP runtime `1.0.0`; its version describes changes to that brick, not changes to the platform. |
| **Third-party workflows and bricks** | Author-controlled. Workflow authors choose their own `version` field and `runtime_compatibility` range. |

## 3. Workflow archive compatibility

A workflow archive is installable against a runtime whose version
satisfies the archive's `workflow.runtime_compatibility` field.
This is enforced by the installer (`python -m ncp_runtime.workflows
install`) and is a hard rejection, not a warning.

Consequence: a workflow archive published before NCP 0.4.0 and pinned
`>=0.3.6,<0.4.0` cannot be installed against a 0.4.0 runtime. That is
the intended behavior: `<0.4.0` means `<0.4.0`. To keep running
such an archive, either:

- retain a compatible pre-0.4.0 runtime binary (install the SDK with
  `pip install ncp-runtime==0.1.0`, or `cargo install ncp-runtime
  --version 0.3.6 --locked`), or
- republish the workflow with a `runtime_compatibility` range that
  includes 0.4.0 (for example `>=0.4.0,<0.5.0`). This is a new
  archive with a new workflow `version`; the runtime does not
  auto-upgrade archives on disk.

## 4. Path to NCP 1.0.0

Starting with 0.4.0, the four maintained implementation packages
advance together on coordinated NCP releases. Each coordinated bump
is preceded by a validated test pass across the cohort on the
supported platforms.

NCP 1.0.0 will designate a supported, production-ready contract
across these four packages, not just a GitHub tag. The outcomes
required before 1.0.0:

- stable, documented public interfaces with clear compatibility
  guarantees,
- a runtime and supported adapters that operate together across
  the supported platforms,
- reliable workflow installation, version management, and recovery,
- verified security boundaries, with no overstated guarantees about
  untrusted WASM,
- external adoption or integration evidence sufficient to validate
  the interfaces,
- a reviewed protocol 1.0.0 contract and tested
  compatibility/migration rules.

Arbitrary intermediate releases are not required. The cohort can
advance from 0.4.0 to 0.5.0 and then to 1.0.0 when those criteria
are met.

## 5. Which SDK version works with which runtime binary?

Current:

- Python SDK (`ncp-runtime` on PyPI) **0.4.0**: binary **0.4.0** only.

Historical:

- Python SDK **0.1.0** (shipped 2026-11-02): binary **0.3.6** only.
