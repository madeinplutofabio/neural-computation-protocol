# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""CLI dispatcher for ncp_runtime.workflows.

Usage:
    python -m ncp_runtime.workflows <subcommand> [options]

Subcommands (D1 + D2):
    package   produce a workflow archive
    install   install an archive by (name, version)
    list      enumerate installed workflows
    show      print workflow.json + brick summary for one install
    remove    delete installed workflow(s)
    resolve   print the (graph, brick_dir) paths for an install

D3 adds: mcp-config.
"""

from __future__ import annotations

import argparse
import json
import sys

from ncp_runtime.types import NCPWorkflowError


def _cmd_package(args: argparse.Namespace) -> int:
    from ncp_runtime.workflows.pack import package

    r = package(
        graph=args.graph,
        brick_dir=args.brick_dir,
        name=args.name,
        version=args.version,
        runtime_compatibility=args.runtime_compat,
        description=args.description,
        out=args.out,
    )
    print(f"packaged:   {r['path']}")
    print(f"size:       {r['size_bytes']} bytes")
    print(f"sha256:     {r['sha256']}")
    print(f"name:       {r['name']}")
    print(f"version:    {r['version']}")
    print(f"bricks ({len(r['bricks'])}):")
    for b in r["bricks"]:
        print(f"  - {b['brick_id']}  ({b['digest']}, {b['size_bytes']} bytes)")
    return 0


def _cmd_install(args: argparse.Namespace) -> int:
    from ncp_runtime.workflows.install import install

    r = install(args.archive, sha256=args.sha256, binary=args.binary)
    print(f"installed:  {r['install_root']}")
    print(f"name:       {r['name']}")
    print(f"version:    {r['version']}")
    print(f"sha256:     {r['sha256']}")
    print(f"source:     {r['source']}")
    print(f"runtime:    {r['runtime_version']}")
    print(f"bricks ({len(r['bricks'])}):")
    for b in r["bricks"]:
        print(f"  - {b['brick_id']}  ({b['digest']}, {b['size_bytes']} bytes)")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    from ncp_runtime.workflows.install import list_installed

    rows = list_installed()
    if args.json:
        print(
            json.dumps(
                [{**r, "install_root": str(r["install_root"])} for r in rows],
                indent=2,
            )
        )
        return 0
    if not rows:
        print("(no workflows installed)")
        return 0
    for r in rows:
        print(f"{r['name']}  {r['version']}  {r['installed_at']}  {r['source']}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    from ncp_runtime.workflows.install import show

    info = show(args.name, version=args.version)
    if args.json:
        payload = {**info, "install_root": str(info["install_root"])}
        print(json.dumps(payload, indent=2, default=str))
        return 0
    print(f"name:       {info['name']}")
    print(f"version:    {info['version']}")
    print(f"root:       {info['install_root']}")
    print(f"installed:  {info['install_metadata']['installed_at']}")
    print(f"source:     {info['install_metadata']['source']}")
    print(f"sha256:     {info['install_metadata']['sha256']}")
    print(f"description:{info['workflow_json'].get('description', '')}")
    print(f"bricks ({len(info['bricks'])}):")
    for b in info["bricks"]:
        if "error" in b:
            print(f"  - ERROR: {b['error']}")
        else:
            print(f"  - {b['brick_id']}  v{b['version']}  {b['digest']}  {b['size_bytes']} bytes")
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    from ncp_runtime.workflows.install import remove

    removed = remove(args.name, version=args.version, all=args.all)
    for p in removed:
        print(f"removed:    {p}")
    return 0


def _cmd_resolve(args: argparse.Namespace) -> int:
    from ncp_runtime.workflows.install import resolve

    graph, brick_dir = resolve(args.name, version=args.version)
    if args.format == "json":
        print(json.dumps({"graph": str(graph), "brick_dir": str(brick_dir)}, indent=2))
    else:
        print(f"{graph}\t{brick_dir}")
    return 0


def _cmd_mcp_config(args: argparse.Namespace) -> int:
    from ncp_runtime.workflows.mcp import (
        mcp_config,
        mcp_config_json,
        mcp_config_snippet,
    )

    if args.format == "json":
        print(mcp_config_json(args.name, version=args.version, server_command=args.server))
    elif args.format == "snippet":
        print(mcp_config_snippet(args.name, version=args.version, server_command=args.server))
    else:
        cfg = mcp_config(args.name, version=args.version, server_command=args.server)
        # tsv: one line of "<command>\t<arg1>\t<arg2>..." for easy xargs piping.
        tokens = [cfg["command"], *cfg["args"]]
        print("\t".join(tokens))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ncp_runtime.workflows")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("package", help="Package a graph + brick-dir into a workflow archive")
    p.add_argument("--graph", required=True)
    p.add_argument("--brick-dir", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--runtime-compat", required=True)
    p.add_argument("--description", default="")
    p.add_argument("--out", required=True)
    p.set_defaults(func=_cmd_package)

    p = sub.add_parser("install", help="Install a workflow archive")
    p.add_argument("archive", help="local path, file:// URL, or https:// URL")
    p.add_argument("--sha256", required=True, help="expected archive sha256 (hex)")
    p.add_argument("--binary", default=None, help="override the ncp runtime binary")
    p.set_defaults(func=_cmd_install)

    p = sub.add_parser("list", help="List installed workflows")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_list)

    p = sub.add_parser("show", help="Show one installed workflow")
    p.add_argument("name")
    p.add_argument("--version", default=None)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_show)

    p = sub.add_parser("remove", help="Remove installed workflow(s)")
    p.add_argument("name")
    p.add_argument("--version", default=None)
    p.add_argument(
        "--all",
        action="store_true",
        help="remove every installed version of this name",
    )
    p.set_defaults(func=_cmd_remove)

    p = sub.add_parser(
        "resolve",
        help="Print (graph_path, brick_dir) for an installed workflow",
    )
    p.add_argument("name")
    p.add_argument("--version", default=None)
    p.add_argument("--format", choices=("tsv", "json"), default="tsv")
    p.set_defaults(func=_cmd_resolve)

    p = sub.add_parser(
        "mcp-config",
        help="Emit MCP server config for an installed workflow (server is unchanged)",
    )
    p.add_argument("name")
    p.add_argument("--version", default=None)
    p.add_argument(
        "--server",
        default="ncp-mcp-server",
        help="MCP server binary (default: ncp-mcp-server on PATH)",
    )
    p.add_argument(
        "--format",
        choices=("tsv", "json", "snippet"),
        default="snippet",
        help="snippet (default): mcpServers block; json: config dict; tsv: command tokens",
    )
    p.set_defaults(func=_cmd_mcp_config)

    args = ap.parse_args(argv)
    try:
        return int(args.func(args))
    except NCPWorkflowError as e:
        print(f"ncp-runtime workflows: error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
