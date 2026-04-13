<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# Security Policy

## Supported versions

This project is early-stage and evolving quickly.

Security fixes land on:

- the default branch (`main`)
- the latest tagged release (when applicable)

If you are pinning an older tag, you may need to cherry-pick fixes.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security-sensitive reports.

Instead, use one of these channels:

1. **GitHub Security Advisories (preferred)**  
   Go to the repository’s “Security” tab and choose “Report a vulnerability”.

2. If you cannot use GitHub advisories, open a minimal issue titled **“Security contact requested”**.  
   Do not include details. A maintainer will follow up privately.

## What to include

To help reproduce and fix quickly, include:

- A clear description of the issue and impact
- Steps to reproduce (PoC if possible, but keep it minimal)
- Affected versions / commits
- Any relevant environment details (OS, CPU arch, Rust/Node versions)
- Suggested mitigation (if you have one)

## Coordinated disclosure

If you report a vulnerability, we’ll aim to:

- acknowledge receipt
- reproduce and assess severity
- coordinate a fix and disclosure timeline

We may request that you keep the report private until a fix is available.

## Security assumptions (current scope)

NCP’s reference runtime is designed around:

- **WASM sandboxing** (no filesystem/network for bricks by default)
- explicit **resource limits** (time, memory, output size)
- deterministic routing and structured results

However, **integrations** (Phase 3) will widen the threat surface. Treat all adapters and deployment environments as part of your security boundary, and run the benchmark + conformance suites in CI.

## Non-security bugs

For non-sensitive issues (crashes, incorrect routing, schema validation errors), please open a normal GitHub issue.
