# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""SDK version and runtime compatibility pin.

See design doc §4.2 (runtime-SDK compatibility contract). The
pin is exact: v0.1.0 of the SDK supports exactly runtime
``0.3.6``. Any expansion of the supported set must be a
deliberate design decision made in lockstep with an SDK
release, not an incidental edit.
"""

__version__ = "0.1.0"

SUPPORTED_RUNTIME_VERSIONS: tuple[str, ...] = ("0.3.6",)
