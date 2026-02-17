// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, it, expect } from "vitest";
import { validateCrossManifests } from "../src/validators/cross.js";
import type { CrossManifestData } from "../src/validators/cross.js";
import { loadFile } from "../src/loader.js";
import type {
  BrickManifest,
  GraphManifest,
  ValidationSummary,
} from "../src/types.js";

// ── Paths ────────────────────────────────────────────────────────────────────

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const VALID_DIR = path.join(ROOT, "tests", "fixtures", "valid");
const INVALID_DIR = path.join(ROOT, "tests", "fixtures", "invalid");

// ── Helpers ──────────────────────────────────────────────────────────────────

function loadCrossData(
  graphPath: string,
  brickPaths: string[],
): CrossManifestData {
  const graph = loadFile<GraphManifest>(graphPath);
  const bricks = new Map<string, BrickManifest>();
  for (const bp of brickPaths) {
    const brick = loadFile<BrickManifest>(bp);
    bricks.set(brick.brick_id, brick);
  }
  return { graph, bricks };
}

/** Assert invariant-negative: specific rule fails. */
function expectInvariantFail(
  summary: ValidationSummary,
  ruleId: string,
): void {
  expect(summary.schema_valid).toBe(true);
  expect(summary.valid).toBe(false);
  expect(summary.results.length).toBeGreaterThan(0);
  // No schema results (cross validator never emits them)
  expect(
    summary.results.some((r) => r.rule.startsWith("schema:")),
  ).toBe(false);
  // Specific invariant failed
  expect(
    summary.results.some((r) => r.rule === ruleId && r.status === "fail"),
  ).toBe(true);
}

/** Assert warning-only: valid is true, but a warning exists for the rule. */
function expectWarningOnly(
  summary: ValidationSummary,
  ruleId: string,
): void {
  expect(summary.schema_valid).toBe(true);
  expect(summary.valid).toBe(true);
  expect(summary.results.length).toBeGreaterThan(0);
  expect(summary.summary.failed).toBe(0);
  expect(summary.summary.warnings).toBeGreaterThan(0);
  // No schema results
  expect(
    summary.results.some((r) => r.rule.startsWith("schema:")),
  ).toBe(false);
  // Specific warning exists
  expect(
    summary.results.some(
      (r) =>
        r.rule === ruleId &&
        r.status === "fail" &&
        r.severity === "warning",
    ),
  ).toBe(true);
}

// ── Valid fixtures ───────────────────────────────────────────────────────────

describe("valid cross-manifest validation", () => {
  it("support-routing + both bricks passes all checks", () => {
    const data = loadCrossData(
      path.join(VALID_DIR, "support-routing-graph.yaml"),
      [
        path.join(VALID_DIR, "sentiment-gate.yaml"),
        path.join(VALID_DIR, "llm-escalation.yaml"),
      ],
    );
    const s = validateCrossManifests(data);
    expect(s.valid).toBe(true);
    expect(s.schema_valid).toBe(true);
    expect(s.summary.failed).toBe(0);
    expect(s.summary.warnings).toBe(0);
  });
});

// ── Invariant-negative fixtures ──────────────────────────────────────────────

describe("invariant-negative cross-manifest fixtures", () => {
  it("CROSS_SLOTS_FULLY_BOUND: missing slot binding", () => {
    const data = loadCrossData(
      path.join(INVALID_DIR, "cross-slots-fully-bound.graph.yaml"),
      [path.join(INVALID_DIR, "cross-slots-fully-bound.brick.yaml")],
    );
    const s = validateCrossManifests(data);
    expectInvariantFail(s, "CROSS_SLOTS_FULLY_BOUND");
  });

  it("CROSS_SLOT_TYPES_MATCH: consistency mismatch (LIVE_OPS vs PINNED)", () => {
    const data = loadCrossData(
      path.join(INVALID_DIR, "cross-slot-types-match.graph.yaml"),
      [path.join(INVALID_DIR, "cross-slot-types-match.brick.yaml")],
    );
    const s = validateCrossManifests(data);
    expectInvariantFail(s, "CROSS_SLOT_TYPES_MATCH");
  });

  it("CROSS_VERSION_SATISFIES: version mismatch (=2.0.0 vs 1.0.0)", () => {
    const data = loadCrossData(
      path.join(INVALID_DIR, "cross-version-satisfies.graph.yaml"),
      [path.join(INVALID_DIR, "cross-version-satisfies.brick.yaml")],
    );
    const s = validateCrossManifests(data);
    expectInvariantFail(s, "CROSS_VERSION_SATISFIES");
  });
});

// ── Warning-only fixtures ────────────────────────────────────────────────────

describe("warning-only cross-manifest fixtures", () => {
  it("CROSS_BRICK_MANIFEST_AVAILABLE: missing brick manifest", () => {
    const data = loadCrossData(
      path.join(INVALID_DIR, "cross-brick-manifest-available.graph.yaml"),
      [], // no brick manifests provided
    );
    const s = validateCrossManifests(data);
    expectWarningOnly(s, "CROSS_BRICK_MANIFEST_AVAILABLE");
  });
});
