<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# Installing NCP

Three ways to get a working `ncp` CLI in under 5 minutes. Pick the one that fits your OS + arch.

## Architecture coverage

| OS / arch | GitHub Release archive | Docker | `cargo install` |
|---|---|---|---|
| Linux x86_64 | ✅ `ncp_v0.3.5_linux_x86_64.tar.gz` | ✅ `ghcr.io/madeinplutofabio/ncp:v0.3.5` | ✅ |
| Linux aarch64 | ❌ ([Phase 3C](ROADMAP.md#5-phase-3c--production-profile-hardening-track)) | ❌ ([Phase 3C](ROADMAP.md#5-phase-3c--production-profile-hardening-track)) | ✅ |
| macOS aarch64 (Apple Silicon, M1+) | ✅ `ncp_v0.3.5_macos_aarch64.tar.gz` | n/a | ✅ |
| **macOS x86_64 (Intel)** | ❌ ([Phase 3C](ROADMAP.md#5-phase-3c--production-profile-hardening-track)) | n/a | ✅ |
| Windows x86_64 | ✅ `ncp_v0.3.5_windows_x86_64.zip` | n/a | ✅ |

**Note for macOS Intel users:** the release archive targets Apple Silicon only. Use `cargo install` (below) or build from source. Multi-arch coverage is on the [roadmap](ROADMAP.md#5-phase-3c--production-profile-hardening-track) for Phase 3C.

---

## Option 1 — Download from GitHub Releases (no toolchain required)

1. Go to https://github.com/madeinplutofabio/neural-computation-protocol/releases/latest
2. Download the archive matching your platform (see table above).
3. Verify the checksum against `SHA256SUMS` (also attached to the release):

   **Linux / macOS:**
   ```bash
   shasum -a 256 -c SHA256SUMS --ignore-missing
   ```

   **Windows (PowerShell):**
   ```powershell
   Get-FileHash -Algorithm SHA256 ncp_v0.3.5_windows_x86_64.zip
   # Compare the output against the line for windows_x86_64 in SHA256SUMS
   ```

4. Extract:

   **Linux / macOS:**
   ```bash
   tar -xzf ncp_v0.3.5_<platform>.tar.gz
   cd ncp_v0.3.5_<platform>
   ```

   **Windows:**
   ```powershell
   Expand-Archive ncp_v0.3.5_windows_x86_64.zip
   cd ncp_v0.3.5_windows_x86_64
   ```

5. Run a quick verify (see **Quick verify** at the bottom of this doc).

Each archive contains the `ncp` binary, the `examples/` tree, plus `LICENSE`, `NOTICE`, and `README.md`.

---

## Option 2 — Docker (Linux x86_64)

Image base: `gcr.io/distroless/cc-debian12:nonroot` (small distroless runtime image; no shell, runs as non-root). The image bakes in `/app/examples/` (WORKDIR = `/app`) so example paths just work — no bind-mount needed.

```bash
# Pull
docker pull ghcr.io/madeinplutofabio/ncp:v0.3.5

# Verify
docker run --rm ghcr.io/madeinplutofabio/ncp:v0.3.5 --version

# Run the quick-verify example (examples/ are inside the image)
docker run --rm ghcr.io/madeinplutofabio/ncp:v0.3.5 \
  run examples/graphs/echo-pipeline/graph.yaml \
  --input examples/graphs/echo-pipeline/sample.json
```

To run your own graph, mount a host directory:

```bash
docker run --rm -v "$PWD/my-graphs:/work:ro" -w /work \
  ghcr.io/madeinplutofabio/ncp:v0.3.5 \
  run my-graph.yaml --input my-input.json
```

**Tag conventions** (strict-pin only):
- Each release publishes **two equivalent** exact-version tags: `v0.3.5` (matches the git tag) and `0.3.5` (semver-canonical). Same image digest.
- **No `:latest`**, no floating `:0.3` or `:0` — always specify a full version.
- Platform: **`linux/amd64` only** (Linux aarch64 / multi-arch deferred to [Phase 3C](ROADMAP.md#5-phase-3c--production-profile-hardening-track)).

---

## Option 3 — `cargo install` from crates.io

Requires Rust 1.94+ (the project's pinned toolchain).

```bash
cargo install ncp-runtime --bin ncp --locked
ncp --version
```

`--locked` ensures cargo uses the exact dependency versions that shipped with the release (the lockfile travels inside the `.crate` for binary packages).

The crates.io package installs only the `ncp` binary — `examples/` are NOT included. To run the quick-verify example, either:
- Clone the repo: `git clone https://github.com/madeinplutofabio/neural-computation-protocol.git && cd neural-computation-protocol`
- Or download just the examples from a GitHub Release archive.

---

## Option 4 — Build from source

```bash
git clone https://github.com/madeinplutofabio/neural-computation-protocol.git
cd neural-computation-protocol
cargo build -p ncp-runtime --release --bin ncp
./target/release/ncp --version
```

For the full developer workflow (running tests, building bricks, benchmarks), see [`docs/ADOPTION_GUIDE.md`](ADOPTION_GUIDE.md).

---

## Quick verify

Run the deterministic echo example on any install path:

```bash
ncp run examples/graphs/echo-pipeline/graph.yaml --input examples/graphs/echo-pipeline/sample.json
```

(From within the extracted archive, the cloned repo, or the Docker image's working directory.)

Expected: a JSON result emitted to stdout with a `Success` terminal. If you see that, your install works.

For more end-to-end tutorials (tracing, the hybrid-routing demo, embedding `ncp` as a library), see [`docs/ADOPTION_GUIDE.md`](ADOPTION_GUIDE.md).

---

## Next steps

- **Adopting NCP in your stack:** [`docs/ADOPTION_GUIDE.md`](ADOPTION_GUIDE.md)
- **Benchmark methodology + numbers:** [`BENCHMARK.md`](../BENCHMARK.md)
- **Phase roadmap:** [`docs/ROADMAP.md`](ROADMAP.md)
- **Contributing:** [`CONTRIBUTING.md`](../CONTRIBUTING.md)
