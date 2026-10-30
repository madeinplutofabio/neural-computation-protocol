# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori

"""Python SDK for the NCP runtime (`ncp` CLI, v0.3.6).

Primary API:

    from ncp_runtime import NCP
    ncp = NCP()
    with ncp.run(graph=..., brick_dir=..., inputs={...}) as result:
        if result.result_type == "Success":
            print(result.terminals[0].output)

Each `run()` spawns one `ncp` subprocess (one subprocess per call in
v0.1.0). Graph-level outcomes, including `result_type == "Failure"`,
are normal returns; only infrastructure problems raise, as subclasses
of `NCPSDKError`.

See `docs/PYTHON_SDK.md` for the full contract.
"""

from ncp_runtime._version import SUPPORTED_RUNTIME_VERSIONS, __version__
from ncp_runtime.runner import NCP
from ncp_runtime.trace import (
    InvokeRecord,
    RuntimeInfoRecord,
    TraceRecord,
    UnknownTraceRecord,
)
from ncp_runtime.types import (
    NCPBinaryNotFoundError,
    NCPRuntimeVersionError,
    NCPSDKError,
    NCPSubprocessError,
    NCPTimeoutError,
    NCPTraceSchemaError,
    Result,
    Terminal,
    TerminalError,
)

__all__ = (
    # Version + compatibility
    "__version__",
    "SUPPORTED_RUNTIME_VERSIONS",
    # Entry point
    "NCP",
    # Result + terminal values
    "Result",
    "Terminal",
    "TerminalError",
    # Trace iterator variants (schema version "ncp-trace-0.1")
    "RuntimeInfoRecord",
    "InvokeRecord",
    "UnknownTraceRecord",
    "TraceRecord",
    # Exception hierarchy
    "NCPSDKError",
    "NCPBinaryNotFoundError",
    "NCPRuntimeVersionError",
    "NCPTimeoutError",
    "NCPSubprocessError",
    "NCPTraceSchemaError",
)
