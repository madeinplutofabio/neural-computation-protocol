# syntax=docker/dockerfile:1.7
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori
#
# Dockerfile for the NCP reference runtime (ncp CLI).
#
# Two stages:
#   1. `build`   — full Rust 1.94 toolchain on Debian bookworm; produces a
#                  static-friendly release binary of the `ncp` bin.
#   2. final     — distroless Debian 12 with libc + libgcc_s (no shell, no
#                  package manager, runs as non-root). Small distroless final image.
#
# Final image layout:
#   /usr/local/bin/ncp        — the CLI (ENTRYPOINT)
#   /app/examples/graphs/**   — runnable example graphs
#   /app/examples/bricks/**   — committed WASM bricks (digest-gated by
#                              validate.yml's wasm-digest-check)
#   /app/LICENSE              — Apache 2.0 (compliance §4(d))
#   /app/NOTICE               — project notice
#
# Usage (post-publish):
#   docker run --rm ghcr.io/madeinplutofabio/ncp:v0.3.4 --version
#   docker run --rm ghcr.io/madeinplutofabio/ncp:v0.3.4 run \
#     examples/graphs/echo-pipeline/graph.yaml \
#     --input examples/graphs/echo-pipeline/sample.json
#
# Note: WASM brick artifacts are NOT rebuilt during this image build —
# the committed examples/bricks/**/*.wasm files are used verbatim (their
# byte-for-byte source-rebuild fidelity is already gated by
# validate.yml's wasm-digest-check on every PR/push to main).

# ──────────────────────────────────────────────────────────────────────
# Stage 1: build
# ──────────────────────────────────────────────────────────────────────
FROM rust:1.94-bookworm AS build

WORKDIR /src

# Copy the entire build context (filtered by .dockerignore — see that file
# for what's excluded; runtime/, bricks/*, Cargo.toml, Cargo.lock,
# rust-toolchain.toml, examples/, LICENSE, NOTICE are included).
COPY . .

# Build the release binary with --locked to ensure the published image
# uses the exact dependency versions committed in Cargo.lock.
RUN cargo build -p ncp-runtime --release --locked --bin ncp

# ──────────────────────────────────────────────────────────────────────
# Stage 2: final (distroless)
# ──────────────────────────────────────────────────────────────────────
FROM gcr.io/distroless/cc-debian12:nonroot

# OCI image labels (rendered as image metadata; visible via `docker inspect`)
LABEL org.opencontainers.image.title="ncp"
LABEL org.opencontainers.image.description="NCP reference runtime — composable, auditable WASM agent graphs"
LABEL org.opencontainers.image.source="https://github.com/madeinplutofabio/neural-computation-protocol"
LABEL org.opencontainers.image.url="https://github.com/madeinplutofabio/neural-computation-protocol"
LABEL org.opencontainers.image.documentation="https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/docs/INSTALL.md"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.vendor="madeinpluto"

WORKDIR /app

# Binary at a standard CLI install location.
COPY --from=build /src/target/release/ncp /usr/local/bin/ncp

# Examples baked in so `docker run … ncp run examples/…` works without a
# bind-mount. Relative paths resolve under WORKDIR=/app.
COPY --from=build /src/examples /app/examples

# Apache 2.0 §4(d): NOTICE must accompany redistributed binaries.
COPY --from=build /src/LICENSE /app/LICENSE
COPY --from=build /src/NOTICE /app/NOTICE

# Distroless `:nonroot` runs as UID 65532 by default. Files copied via
# COPY get root ownership but world-readable perms (644), so the nonroot
# user can read them. No chown step needed.

ENTRYPOINT ["/usr/local/bin/ncp"]
