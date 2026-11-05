# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Single source of truth for `__version__`.

This module is private (leading underscore). Both
:mod:`ncp_langgraph.__init__` and
:mod:`ncp_langgraph.subprocess_runner` import `__version__` from here
so the version string has exactly one authoritative location and no
circular-import risk arises from the public package surface importing
from internal modules that also need the version.

Starting with NCP 0.4.0, this adapter advances together with the
other maintained implementation packages as a coordinated release
cohort (see ``docs/COMPATIBILITY.md``).
"""

__version__ = "0.4.0"
