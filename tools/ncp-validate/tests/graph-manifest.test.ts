// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, it, expect } from "vitest";
import { validateGraphManifest } from "../src/validators/graph-manifest.js";
import { loadFile } from "../src/loader.js";
import type { GraphManifest, ValidationSummary } from "../src/types.js";

// ── Paths ────────────────────────────────────────────────────────────────────

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const VALID_DIR = path.join(ROOT, "tests", "fixtures", "valid");
const INVALID_DIR = path.join(ROOT, "tests", "fixtures", "invalid");

// ── Helpers ──────────────────────────────────────────────────────────────────

function validateGraphFixture(filePath: string): ValidationSummary {
  const manifest = loadFile<GraphManifest>(filePath);
  return validateGraphManifest(manifest, filePath);
}

/** Assert invariant-negative: schema passes, specific rule fails. */
function expectInvariantFail(
  summary: ValidationSummary,
  ruleId: string,
): void {
  expect(summary.schema_valid).toBe(true);
  expect(summary.valid).toBe(false);
  expect(summary.results.length).toBeGreaterThan(0);
  // No schema failures
  expect(
    summary.results.some(
      (r) => r.rule.startsWith("schema:") && r.status === "fail",
    ),
  ).toBe(false);
  // Specific invariant failed
  expect(
    summary.results.some((r) => r.rule === ruleId && r.status === "fail"),
  ).toBe(true);
}

// ── Valid fixtures ───────────────────────────────────────────────────────────

describe("valid graph manifests", () => {
  it("support-routing passes all checks", () => {
    const s = validateGraphFixture(
      path.join(VALID_DIR, "support-routing-graph.yaml"),
    );
    expect(s.valid).toBe(true);
    expect(s.schema_valid).toBe(true);
    expect(s.summary.failed).toBe(0);
    expect(s.summary.warnings).toBe(0);
  });
});

// ── Invariant-negative fixtures ──────────────────────────────────────────────

describe("invariant-negative graph fixtures", () => {
  it("GRAPH_NODE_IDS_UNIQUE: duplicate node_id", () => {
    const s = validateGraphFixture(
      path.join(INVALID_DIR, "graph-node-ids-unique.yaml"),
    );
    expectInvariantFail(s, "GRAPH_NODE_IDS_UNIQUE");
  });

  it("GRAPH_EDGE_IDS_UNIQUE: duplicate edge_id", () => {
    const s = validateGraphFixture(
      path.join(INVALID_DIR, "graph-edge-ids-unique.yaml"),
    );
    expectInvariantFail(s, "GRAPH_EDGE_IDS_UNIQUE");
  });

  it("GRAPH_EDGE_SOURCE_EXISTS: dangling source_node", () => {
    const s = validateGraphFixture(
      path.join(INVALID_DIR, "graph-edge-source-exists.yaml"),
    );
    expectInvariantFail(s, "GRAPH_EDGE_SOURCE_EXISTS");
  });

  it("GRAPH_EDGE_TARGET_EXISTS: dangling target_node", () => {
    const s = validateGraphFixture(
      path.join(INVALID_DIR, "graph-edge-target-exists.yaml"),
    );
    expectInvariantFail(s, "GRAPH_EDGE_TARGET_EXISTS");
  });

  it("GRAPH_NO_SELF_EDGES: self-loop edge", () => {
    const s = validateGraphFixture(
      path.join(INVALID_DIR, "graph-no-self-edges.yaml"),
    );
    expectInvariantFail(s, "GRAPH_NO_SELF_EDGES");
  });

  it("GRAPH_ON_ERROR_TARGETS_EXIST: on_error references ghost node", () => {
    const s = validateGraphFixture(
      path.join(INVALID_DIR, "graph-on-error-targets-exist.yaml"),
    );
    expectInvariantFail(s, "GRAPH_ON_ERROR_TARGETS_EXIST");
  });

  it("GRAPH_QUORUM_VALID: quorum_n > quorum_m", () => {
    const s = validateGraphFixture(
      path.join(INVALID_DIR, "graph-quorum-valid.yaml"),
    );
    expectInvariantFail(s, "GRAPH_QUORUM_VALID");
  });

  it("GRAPH_PRIORITY_EDGES_HAVE_PRIORITY: priority node without edge priority", () => {
    const s = validateGraphFixture(
      path.join(INVALID_DIR, "graph-priority-edges-have-priority.yaml"),
    );
    expectInvariantFail(s, "GRAPH_PRIORITY_EDGES_HAVE_PRIORITY");
  });
});
