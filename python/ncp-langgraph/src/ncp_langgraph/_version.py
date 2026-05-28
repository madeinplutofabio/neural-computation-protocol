# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Single source of truth for `__version__`.

This module is private (leading underscore). Both
:mod:`ncp_langgraph.__init__` and
:mod:`ncp_langgraph.subprocess_runner` import `__version__` from here
so the version string has exactly one authoritative location and no
circular-import risk arises from the public package surface importing
from internal modules that also need the version.

PR F bumps this from ``"0.1.0.dev0"`` to ``"0.1.0"`` as part of the
publish ceremony (mirrors the bump in ``pyproject.toml``).
"""

__version__ = "0.1.0"
