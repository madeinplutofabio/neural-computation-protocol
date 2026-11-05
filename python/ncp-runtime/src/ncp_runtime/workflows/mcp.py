# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Generate an MCP host-config for an installed workflow.

The MCP server (`ncp-mcp-server`) is unchanged by this module: it already
accepts `--graph <PATH>` and `--brick-dir <PATH>`. This module just emits
those two flags, pointing at the paths that `install.resolve()` returns.

Three output shapes:
  * `python` (default for the Python API): a dict of the form
      {"command": <mcp-server-path>, "args": [...], "env": {}}
    suitable for MCP desktop hosts that consume that record.
  * `json`: the same dict as a pretty-printed JSON string.
  * `snippet`: a copy-pasteable snippet keyed by the workflow name under
      "mcpServers", matching the shape MCP desktop hosts (Claude Desktop,
      Cursor, Continue) use for stdio servers.

`server_command` defaults to `"ncp-mcp-server"` (resolved on PATH by the
host) but can be overridden with the --server flag or kwarg so users can
point at a specific absolute binary — this does not modify the server
in any way.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def mcp_config(
    name: str,
    *,
    version: str | None = None,
    server_command: str | Path = "ncp-mcp-server",
) -> dict[str, Any]:
    """Return an MCP host-config record for an installed workflow.

    Shape:
        {
            "name": "<workflow name>",
            "version": "<resolved version>",
            "command": "<server_command>",
            "args": ["--graph", "<path>", "--brick-dir", "<path>"],
            "env": {},
        }

    Resolves the installation ONCE via `_resolve_single` and derives both
    the version field and the --graph / --brick-dir paths from that single
    row, so a concurrent install completing between two separate lookups
    cannot cause the returned version to disagree with the args or
    produce a spurious NCPWorkflowAmbiguousVersionError.
    """
    from ncp_runtime.workflows.install import _load_workflow_json, _resolve_single

    row = _resolve_single(name, version)
    root = row["install_root"]
    wf = _load_workflow_json(root)
    graph_path = root / wf["graph"]
    brick_dir = root / wf["bricks_dir"]
    return {
        "name": name,
        "version": str(row["version"]),
        "command": str(server_command),
        "args": ["--graph", str(graph_path), "--brick-dir", str(brick_dir)],
        "env": {},
    }


def mcp_config_json(
    name: str,
    *,
    version: str | None = None,
    server_command: str | Path = "ncp-mcp-server",
) -> str:
    """Return the config as a pretty-printed JSON string."""
    return json.dumps(
        mcp_config(name, version=version, server_command=server_command),
        indent=2,
    )


def mcp_config_snippet(
    name: str,
    *,
    version: str | None = None,
    server_command: str | Path = "ncp-mcp-server",
) -> str:
    """Return a `{"mcpServers": {<name>: {command, args, env}}}` snippet
    suitable for pasting into an MCP host config file.
    """
    cfg = mcp_config(name, version=version, server_command=server_command)
    snippet = {
        "mcpServers": {
            cfg["name"]: {
                "command": cfg["command"],
                "args": cfg["args"],
                "env": cfg["env"],
            }
        }
    }
    return json.dumps(snippet, indent=2)
