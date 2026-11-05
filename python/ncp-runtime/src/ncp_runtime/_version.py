# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""SDK version and runtime compatibility pin.

Starting with NCP 0.4.0, the four maintained implementation
packages (Rust ``ncp-runtime``, Python ``ncp-runtime`` SDK,
``ncp-mcp-server``, ``ncp-langgraph``) advance together as a
coordinated platform release cohort. See
``docs/COMPATIBILITY.md`` for the version matrix and policy.

The pin remains exact: v0.4.0 of the SDK supports exactly runtime
``0.4.0``. Any expansion of the supported set must be a
deliberate design decision made in lockstep with a coordinated
release, not an incidental edit.
"""

__version__ = "0.4.0"

SUPPORTED_RUNTIME_VERSIONS: tuple[str, ...] = ("0.4.0",)
