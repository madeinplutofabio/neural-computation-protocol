// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, it, expect } from "vitest";
import { validateBrickManifest } from "../src/validators/brick-manifest.js";
import { loadFile } from "../src/loader.js";
import type { BrickManifest, ValidationSummary } from "../src/types.js";

// ── Paths ────────────────────────────────────────────────────────────────────

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const VALID_DIR = path.join(ROOT, "tests", "fixtures", "valid");
const INVALID_DIR = path.join(ROOT, "tests", "fixtures", "invalid");

// ── Helpers ──────────────────────────────────────────────────────────────────

function validateBrickFixture(filePath: string): ValidationSummary {
  const manifest = loadFile<BrickManifest>(filePath);
  return validateBrickManifest(manifest, filePath);
}

/** Assert schema-negative: schema catches the violation, invariants skipped. */
function expectSchemaInvalid(summary: ValidationSummary): void {
  expect(summary.schema_valid).toBe(false);
  expect(summary.valid).toBe(false);
  expect(summary.results.length).toBeGreaterThan(0);
  // At least one schema error exists
  expect(
    summary.results.some(
      (r) => r.rule.startsWith("schema:") && r.status === "fail",
    ),
  ).toBe(true);
  // No invariant results (skipped when schema fails)
  const nonSchema = summary.results.filter(
    (r) => !r.rule.startsWith("schema:"),
  );
  expect(nonSchema.length).toBe(0);
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

describe("valid brick manifests", () => {
  it("sentiment-gate passes all checks", () => {
    const s = validateBrickFixture(path.join(VALID_DIR, "sentiment-gate.yaml"));
    expect(s.valid).toBe(true);
    expect(s.schema_valid).toBe(true);
    expect(s.summary.failed).toBe(0);
    expect(s.summary.warnings).toBe(0);
  });

  it("llm-escalation passes all checks", () => {
    const s = validateBrickFixture(path.join(VALID_DIR, "llm-escalation.yaml"));
    expect(s.valid).toBe(true);
    expect(s.schema_valid).toBe(true);
    expect(s.summary.failed).toBe(0);
    expect(s.summary.warnings).toBe(0);
  });
});

// ── Schema-negative fixtures ─────────────────────────────────────────────────

describe("schema-negative brick fixtures", () => {
  it("BRICK_CARRY_NULL_IFF_NONE: none class + non-null carry_state", () => {
    const s = validateBrickFixture(
      path.join(INVALID_DIR, "brick-carry-null-iff-none.yaml"),
    );
    expectSchemaInvalid(s);
  });

  it("BRICK_CARRY_NONE_ZERO_BYTES: none class + non-zero max_bytes", () => {
    const s = validateBrickFixture(
      path.join(INVALID_DIR, "brick-carry-none-zero-bytes.yaml"),
    );
    expectSchemaInvalid(s);
  });

  it("BRICK_WALL_NOT_BIT_EXACT: wall_bounded + bit_exact", () => {
    const s = validateBrickFixture(
      path.join(INVALID_DIR, "brick-wall-not-bit-exact.yaml"),
    );
    expectSchemaInvalid(s);
  });

  it("BRICK_DRIFT_NEEDS_EPSILON: bounded_drift without epsilon", () => {
    const s = validateBrickFixture(
      path.join(INVALID_DIR, "brick-drift-needs-epsilon.yaml"),
    );
    expectSchemaInvalid(s);
  });

  it("BRICK_STOCHASTIC_NEEDS_SEED: stochastic without seed_source", () => {
    const s = validateBrickFixture(
      path.join(INVALID_DIR, "brick-stochastic-needs-seed.yaml"),
    );
    expectSchemaInvalid(s);
  });

  it("BRICK_MODEL_PIN_NEEDS_STOCHASTIC: model_pin + non-stochastic mode", () => {
    const s = validateBrickFixture(
      path.join(INVALID_DIR, "brick-model-pin-needs-stochastic.yaml"),
    );
    expectSchemaInvalid(s);
  });

  it("BRICK_MODEL_PIN_NEEDS_REPRO_LEVEL: model_pin without repro level", () => {
    const s = validateBrickFixture(
      path.join(INVALID_DIR, "brick-model-pin-needs-repro-level.yaml"),
    );
    expectSchemaInvalid(s);
  });

  it("BRICK_REPRO_LEVEL_NEEDS_MODEL_PIN: repro level without model_pin", () => {
    const s = validateBrickFixture(
      path.join(INVALID_DIR, "brick-repro-level-needs-model-pin.yaml"),
    );
    expectSchemaInvalid(s);
  });

  it("BRICK_CAPABILITIES_EMPTY: non-empty capabilities", () => {
    const s = validateBrickFixture(
      path.join(INVALID_DIR, "brick-capabilities-empty.yaml"),
    );
    expectSchemaInvalid(s);
  });

  it("BRICK_FORMAT_WASM: non-wasm format", () => {
    const s = validateBrickFixture(
      path.join(INVALID_DIR, "brick-format-wasm.yaml"),
    );
    expectSchemaInvalid(s);
  });
});

// ── Invariant-negative fixtures ──────────────────────────────────────────────

describe("invariant-negative brick fixtures", () => {
  it("BRICK_CARRY_EXISTS_POSITIVE_BYTES: tiny class + zero max_bytes", () => {
    const s = validateBrickFixture(
      path.join(INVALID_DIR, "brick-carry-exists-positive-bytes.yaml"),
    );
    expectInvariantFail(s, "BRICK_CARRY_EXISTS_POSITIVE_BYTES");
  });

  it("BRICK_SIDE_EFFECTS_NEEDS_CARRY: side_effects set + no carry state", () => {
    const s = validateBrickFixture(
      path.join(INVALID_DIR, "brick-side-effects-needs-carry.yaml"),
    );
    expectInvariantFail(s, "BRICK_SIDE_EFFECTS_NEEDS_CARRY");
  });
});
