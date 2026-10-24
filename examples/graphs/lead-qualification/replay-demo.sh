#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Fabio Marcello Salvadori
#
# Replay demonstration for the lead-qualification example.
#
# Runs three representative fixtures twice each through the real `ncp`
# CLI and compares a stable business-projection of each run. This is
# "repeatable deterministic graph execution": the business output
# (next_action, icp_match, bit-exact score, reasons; or error_class +
# message on Failure) is identical across runs with the same input and
# pinned artifacts. Timestamps, latency, and generated trace/session
# IDs in the JSONL trace are expected to vary and are deliberately
# excluded from the comparison.
#
# This is NOT a cryptographic replay certificate. It is a visible,
# reproducible check that the deterministic business contract holds.
#
# Usage:
#     ./examples/graphs/lead-qualification/replay-demo.sh
#
# Dependencies:
#     - bash
#     - jq
#     - a pre-built ncp binary at ./target/release/ncp
#       (override via `NCP_BIN=/path/to/ncp ./replay-demo.sh`)

set -uo pipefail
# NOTE: -e is deliberately NOT set. The failure fixture is expected to
# make `ncp run` exit 1; we need to inspect that exit code rather than
# aborting the script. Each `run_once` validates the exit code explicitly.

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
readonly NCP="${NCP_BIN:-$REPO_ROOT/target/release/ncp}"
readonly GRAPH="$REPO_ROOT/examples/graphs/lead-qualification/graph.yaml"
readonly BRICKS="$REPO_ROOT/examples/bricks"
readonly SAMPLES="$REPO_ROOT/examples/graphs/lead-qualification/samples"

# Stable business projection filter for jq.
#
# -S sorts object keys (deterministic map order on wire). It does NOT
# sort array elements, so the `reasons` array order (part of the
# deterministic contract) is preserved.
readonly STABLE_FILTER='
  if .error then
    { terminal_type: "Failure",
      business: { error_class: .error.error_class,
                  message:     .error.message } }
  else
    { terminal_type: "Success",
      business: { next_action: .next_action,
                  icp_match:   .icp_match,
                  score:       .score,
                  reasons:     .reasons } }
  end'

# Invoke `ncp run` once on a fixture; validate the exit code against the
# expected status (0 for Success terminals, 1 for Failure terminals).
# Reject other statuses, empty output, or non-JSON output.
#
# Prints the stable projection on stdout; prints diagnostics on stderr
# and returns non-zero on any surprise.
run_once() {
    local fixture="$1"
    local expected_exit="$2"
    local stdout status

    # Capture stdout only; discard stderr (trace + resolver chatter are
    # not part of the stable projection). Keep the exit code separately.
    stdout="$("$NCP" run "$GRAPH" --brick-dir "$BRICKS" --input "$fixture" 2>/dev/null)"
    status=$?

    if [ "$status" -ne "$expected_exit" ]; then
        echo "FAIL $(basename "$fixture"): ncp exit $status, expected $expected_exit" >&2
        return 1
    fi
    if [ -z "$stdout" ]; then
        echo "FAIL $(basename "$fixture"): ncp produced empty output" >&2
        return 1
    fi

    local projection
    if ! projection="$(echo "$stdout" | jq -S "$STABLE_FILTER" 2>/dev/null)"; then
        echo "FAIL $(basename "$fixture"): ncp output is not valid JSON matching the projection filter" >&2
        echo "---- raw stdout follows ----" >&2
        echo "$stdout" >&2
        echo "---------------------------" >&2
        return 1
    fi

    printf '%s' "$projection"
}

# Run a fixture twice and compare the two stable projections.
# Prints an OK line summarizing the business outcome on match; prints
# a FAIL line and the diff on mismatch.
replay() {
    local fixture="$1"
    local expected_exit="$2"
    local a b

    a="$(run_once "$fixture" "$expected_exit")" || return 1
    b="$(run_once "$fixture" "$expected_exit")" || return 1

    if [ "$a" = "$b" ]; then
        local summary
        summary="$(echo "$a" | jq -c .business)"
        printf '  OK   %-34s  %s\n' "$(basename "$fixture")" "$summary"
        return 0
    fi
    echo "FAIL $(basename "$fixture"): stable projections differ between runs" >&2
    diff <(echo "$a") <(echo "$b") >&2
    return 1
}

# ---- preflight ----

if ! command -v jq >/dev/null 2>&1; then
    echo "ERROR: jq is required (install via your package manager, e.g. 'brew install jq' or 'apt-get install jq')" >&2
    exit 2
fi
if [ ! -x "$NCP" ]; then
    echo "ERROR: ncp binary not found or not executable at: $NCP" >&2
    echo "       Build it with: cargo build --release -p ncp-runtime --bin ncp" >&2
    echo "       Or override via NCP_BIN=/path/to/ncp" >&2
    exit 2
fi

# ---- run ----

echo "Replay demo -- 3 fixtures x 2 runs each, comparing stable business projection:"
echo "  ncp binary: $NCP"
echo

# Expected exit codes:
#   Success terminals -> ncp exits 0
#   Failure(INVALID_INPUT) terminals -> ncp exits 1
replay "$SAMPLES/qualified_2_of_3.json"     0 || exit 1
replay "$SAMPLES/not_qualified_0_of_3.json" 0 || exit 1
replay "$SAMPLES/failure_bad_type.json"     1 || exit 1

echo
echo "All three fixtures: stable business projections identical across repeat invocations."
