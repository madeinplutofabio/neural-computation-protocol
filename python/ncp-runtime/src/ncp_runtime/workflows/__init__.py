# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Workflow consumption + distribution mechanism for the Python SDK.

Public surface:
  package(...)          - D1: produce a self-contained .tar.gz archive
  install(...)          - D2: install an archive by (name, version)
  list_installed()      - D2: enumerate installed workflows
  show(name, version?)  - D2: inspect one installed workflow
  remove(name, ...)     - D2: delete installed workflow(s)
  resolve(name, ...)    - D2/D3: return (graph_path, brick_dir) for an install
  workflow_home()       - D2: resolve NCP_WORKFLOW_HOME to an absolute path

The installer validates graph + brick references through the Python D1
helpers (pack._validate_brick, pack._collect_brick_refs, pack._check_constraint).
It does not cross-invoke the Rust resolver. Rust retains execution authority
at run time (through NCP.run / NCP.run_workflow).

Requires the `workflows` extra for YAML parsing:
    pip install ncp-runtime[workflows]
"""

from ncp_runtime.workflows.install import (
    INSTALL_SCHEMA_VERSION,
    install,
    list_installed,
    remove,
    resolve,
    show,
    workflow_home,
)
from ncp_runtime.workflows.mcp import mcp_config, mcp_config_json, mcp_config_snippet
from ncp_runtime.workflows.pack import WORKFLOW_SCHEMA_VERSION, package

__all__ = (
    "package",
    "WORKFLOW_SCHEMA_VERSION",
    "install",
    "list_installed",
    "show",
    "remove",
    "resolve",
    "workflow_home",
    "INSTALL_SCHEMA_VERSION",
    "mcp_config",
    "mcp_config_json",
    "mcp_config_snippet",
)
