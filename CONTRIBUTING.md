<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# Contributing to NCP

Thanks for helping make NCP useful in real systems.

If you haven’t already, please read:
- `README.md`
- `BENCHMARK.md`
- `CODE_OF_CONDUCT.md`
- `SECURITY.md`

## Ground rules

- **No CLA.**
- **License:** Apache-2.0.
- **DCO required:** every commit must include `Signed-off-by` (see [`DCO.md`](DCO.md)).
- Be respectful and constructive (see [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)).

## What contributions are most valuable

High-impact contributions right now:

1. **Integrations (Phase 3)**
   - MCP server adapter (one MCP tool = one NCP graph)
   - LangGraph wrapper (an NCP graph as a node)

2. **Brick packs**
   Deterministic bricks that teams can reuse:
   - schema validation / sanitizers
   - extractors / parsers
   - routers / classifiers
   - safety/policy gates

3. **Conformance + test vectors**
   Anything that helps third-party runtimes interoperate correctly.

4. **Docs + examples**
   “How to adopt” guides and realistic example graphs.

## Development setup

### Prereqs

- Rust 1.94+
- Node.js 18+ (for the validator)
- Linux recommended for tight benchmark tails (Windows also supported)

### Build & test (runtime)

```bash
cargo test -p ncp-runtime
cargo build -p ncp-runtime --release
```

### Run the runtime

```bash
cargo run -p ncp-runtime --release -- run examples/graphs/echo-chain/graph.yaml   --input examples/graphs/echo-chain/sample.json
```

### Benchmarks

See `BENCHMARK.md` for the full benchmark matrix and how to reproduce.

Quick sanity run:

```bash
cargo run -p ncp-runtime --release --bin ncp-bench --   examples/graphs/echo-pipeline/graph.yaml   --input examples/graphs/echo-pipeline/sample.json   --warmup 200 --runs 2000
```

### Validator

```bash
cd tools/ncp-validate
npm install
npm run build
node dist/cli.js graph ../../examples/graphs/echo-chain/graph.yaml
```

## Adding a new brick

Bricks are WASM modules that implement the NCP ABI (Model B) and are packaged with:

- a `manifest.yaml`
- an artifact digest + size
- optional JSON schemas for input/output

A good way to start is to copy `bricks/echo` as a template.

When you add a brick:
- keep it deterministic unless you explicitly declare otherwise in the manifest
- keep limits realistic (`max_ms`, `max_mem_mb`, output size)
- add an example graph under `examples/graphs/`

## Pull requests

- Keep PRs focused and well-scoped.
- Add or update tests when changing runtime behavior.
- Update docs when changing CLI flags, bench output fields, or schema semantics.

## Reporting security issues

Please follow `SECURITY.md`. Do not disclose vulnerabilities in public issues.

## License

By contributing, you agree that your contributions will be licensed under Apache-2.0.
