// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

/**
 * Programmatic API for ncp-validate.
 * Re-exports all public validators, invariant rules, and types.
 */

// ── Validators ──────────────────────────────────────────────────────────────

export { validateBrickManifest } from "./validators/brick-manifest.js";
export { validateGraphManifest } from "./validators/graph-manifest.js";
export { validateInvocationEnvelope } from "./validators/envelope.js";
export { validateResult } from "./validators/result.js";
export {
  validateCrossManifests,
  type CrossManifestData,
} from "./validators/cross.js";

// ── Invariant rules (for custom tooling / selective execution) ──────────────

export { brickInvariants } from "./invariants/brick-invariants.js";
export { graphInvariants } from "./invariants/graph-invariants.js";
export { crossInvariants } from "./invariants/cross-invariants.js";

// ── Rule framework ──────────────────────────────────────────────────────────

export { runRules, pass, fail } from "./rules.js";
export type {
  InvariantRule,
  RuleMeta,
  RuleTarget,
  RuleId,
} from "./rules.js";

// ── Loader (file loading only — schema resolution is internal) ──────────────

export { loadFile, resolveRelativeTo } from "./loader.js";

// ── Types ───────────────────────────────────────────────────────────────────

export type {
  BrickManifest,
  GraphManifest,
  InvocationEnvelope,
  Result,
  ValidationResult,
  ValidationSummary,
  RuleStatus,
  RuleSeverity,
} from "./types.js";
