// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

/**
 * Cross-manifest invariant rules (§6.10 / §7.2).
 * These validate consistency between a Graph manifest and the
 * Brick manifests it references.
 *
 * These rules are only run when both Graph and Brick schema
 * validation passes.
 */

import type {
  GraphManifest,
  BrickManifest,
  ValidationResult,
} from "../types.js";
import type { InvariantRule } from "../rules.js";
import { pass, fail } from "../rules.js";

// ── Data type for cross-manifest validation ─────────────────────────────────

export interface CrossManifestData {
  graph: GraphManifest;
  /** Resolved Brick manifests keyed by brick_id. */
  bricks: Map<string, BrickManifest>;
}

// ── SemVer helpers (no external dependency) ─────────────────────────────────

interface SemVer {
  major: number;
  minor: number;
  patch: number;
}

function parseSemver(s: string): SemVer | null {
  const m = s.match(/^(\d+)\.(\d+)\.(\d+)$/);
  if (!m) return null;
  return { major: +m[1], minor: +m[2], patch: +m[3] };
}

function compareSemver(a: SemVer, b: SemVer): number {
  if (a.major !== b.major) return a.major - b.major;
  if (a.minor !== b.minor) return a.minor - b.minor;
  return a.patch - b.patch;
}

/**
 * Compute the upper bound for caret ranges, following real SemVer:
 *   ^1.2.3 → <2.0.0
 *   ^0.2.3 → <0.3.0
 *   ^0.0.3 → <0.0.4
 */
function caretUpper(base: SemVer): SemVer {
  if (base.major !== 0) return { major: base.major + 1, minor: 0, patch: 0 };
  if (base.minor !== 0) return { major: 0, minor: base.minor + 1, patch: 0 };
  return { major: 0, minor: 0, patch: base.patch + 1 };
}

/**
 * Minimal SemVer range check.
 *
 * Supports:
 *   - Exact:    "1.2.3" or "=1.2.3"
 *   - Caret:    "^1.2.3"  (real SemVer semantics, including 0.x)
 *   - Tilde:    "~1.2.3"  (>=1.2.3 <1.3.0)
 *   - Compound: ">=0.2.0 <0.3.0"
 *   - Wildcard: "*"        (always true)
 *
 * Returns null if the range format is not recognised.
 */
function semverSatisfies(version: string, range: string): boolean | null {
  const parsed = parseSemver(version);
  if (!parsed) return null;

  const trimmed = range.trim();

  // Wildcard
  if (trimmed === "*") return true;

  // Compound: ">=X.Y.Z <A.B.C" (flexible whitespace)
  const compound = trimmed.match(
    /^>=\s*(\d+\.\d+\.\d+)\s*<\s*(\d+\.\d+\.\d+)$/,
  );
  if (compound) {
    const lo = parseSemver(compound[1]);
    const hi = parseSemver(compound[2]);
    if (!lo || !hi) return null;
    return compareSemver(parsed, lo) >= 0 && compareSemver(parsed, hi) < 0;
  }

  // Caret: "^X.Y.Z"
  const caret = trimmed.match(/^\^(\d+\.\d+\.\d+)$/);
  if (caret) {
    const base = parseSemver(caret[1]);
    if (!base) return null;
    const upper = caretUpper(base);
    return compareSemver(parsed, base) >= 0 && compareSemver(parsed, upper) < 0;
  }

  // Tilde: "~X.Y.Z"
  const tilde = trimmed.match(/^~(\d+\.\d+\.\d+)$/);
  if (tilde) {
    const base = parseSemver(tilde[1]);
    if (!base) return null;
    const upper = { major: base.major, minor: base.minor + 1, patch: 0 };
    return compareSemver(parsed, base) >= 0 && compareSemver(parsed, upper) < 0;
  }

  // Exact: "X.Y.Z" or "=X.Y.Z"
  const exact = parseSemver(trimmed.replace(/^=/, ""));
  if (exact) {
    return compareSemver(parsed, exact) === 0;
  }

  return null;
}

// ── Ref-binding shape validation ────────────────────────────────────────────

const VALID_CONSISTENCIES = new Set(["PINNED", "LIVE_OPS"]);

interface RefBinding {
  ref: string;
  consistency?: string;
}

function isRefBinding(value: unknown): value is RefBinding {
  if (typeof value !== "object" || value === null) return false;
  const obj = value as Record<string, unknown>;
  if (typeof obj.ref !== "string") return false;
  if (obj.consistency != null && typeof obj.consistency !== "string") return false;
  return true;
}

// ── Rule definitions ────────────────────────────────────────────────────────

const CROSS_BRICK_MANIFEST_AVAILABLE: InvariantRule<CrossManifestData> = {
  id: "CROSS_BRICK_MANIFEST_AVAILABLE",
  section: "§7.2",
  description: "every node's brick_id must resolve to a Brick manifest for cross validation",
  target: "cross",
  severity: "warning",
  evaluate: (data) => {
    const failures: ValidationResult[] = [];
    for (const node of data.graph.nodes) {
      if (!data.bricks.has(node.brick.brick_id)) {
        failures.push(fail(CROSS_BRICK_MANIFEST_AVAILABLE,
          `node "${node.node_id}" references brick "${node.brick.brick_id}" but no manifest was provided`));
      }
    }
    return failures.length > 0 ? failures : pass(CROSS_BRICK_MANIFEST_AVAILABLE);
  },
};

const CROSS_SLOTS_FULLY_BOUND: InvariantRule<CrossManifestData> = {
  id: "CROSS_SLOTS_FULLY_BOUND",
  section: "§7.2",
  description: "graph bindings must cover all slots declared by each Brick",
  target: "cross",
  evaluate: (data) => {
    const failures: ValidationResult[] = [];

    for (const node of data.graph.nodes) {
      const brick = data.bricks.get(node.brick.brick_id);
      if (!brick) continue; // handled by CROSS_BRICK_MANIFEST_AVAILABLE

      const declaredSlots = new Set(brick.graph_ref_slots.map((s) => s.slot));
      const boundSlots = new Set(
        Object.keys(node.bindings.graph_ref_slot_values),
      );

      for (const slot of declaredSlots) {
        if (!boundSlots.has(slot)) {
          failures.push(fail(CROSS_SLOTS_FULLY_BOUND,
            `node "${node.node_id}" is missing binding for slot "${slot}" ` +
            `(declared by brick "${node.brick.brick_id}")`));
        }
      }
    }
    return failures.length > 0 ? failures : pass(CROSS_SLOTS_FULLY_BOUND);
  },
};

const CROSS_SLOT_TYPES_MATCH: InvariantRule<CrossManifestData> = {
  id: "CROSS_SLOT_TYPES_MATCH",
  section: "§6.10",
  description: "slot bindings must be valid ref objects with matching consistency",
  target: "cross",
  evaluate: (data) => {
    const failures: ValidationResult[] = [];

    for (const node of data.graph.nodes) {
      const brick = data.bricks.get(node.brick.brick_id);
      if (!brick) continue;

      const slotMeta = new Map(
        brick.graph_ref_slots.map((s) => [s.slot, s]),
      );

      for (const [slotName, value] of Object.entries(
        node.bindings.graph_ref_slot_values,
      )) {
        const decl = slotMeta.get(slotName);
        if (!decl) continue; // extra binding — not this rule's concern

        // Binding value must be a ref object: { ref: string, consistency?: string }
        if (!isRefBinding(value)) {
          failures.push(fail(CROSS_SLOT_TYPES_MATCH,
            `node "${node.node_id}" slot "${slotName}" binding must be an object ` +
            `with "ref" (string), got ${typeof value}`));
          continue;
        }

        // If consistency is provided, it must be PINNED or LIVE_OPS
        if (value.consistency != null && !VALID_CONSISTENCIES.has(value.consistency)) {
          failures.push(fail(CROSS_SLOT_TYPES_MATCH,
            `node "${node.node_id}" slot "${slotName}" has invalid consistency ` +
            `"${value.consistency}" (expected PINNED or LIVE_OPS)`));
          continue;
        }

        // Missing consistency: warn (runtime assumes Brick's declared value)
        if (value.consistency == null) {
          failures.push({
            rule: CROSS_SLOT_TYPES_MATCH.id,
            section: CROSS_SLOT_TYPES_MATCH.section,
            description: CROSS_SLOT_TYPES_MATCH.description,
            status: "fail",
            severity: "warning",
            message: `node "${node.node_id}" slot "${slotName}" omits consistency; ` +
              `assuming "${decl.ref_consistency}"`,
          });
        } else if (value.consistency !== decl.ref_consistency) {
          // Consistency mismatch: error
          failures.push(fail(CROSS_SLOT_TYPES_MATCH,
            `node "${node.node_id}" slot "${slotName}" consistency is ` +
            `"${value.consistency}" but brick declares "${decl.ref_consistency}"`));
        }
      }
    }
    return failures.length > 0 ? failures : pass(CROSS_SLOT_TYPES_MATCH);
  },
};

const CROSS_VERSION_SATISFIES: InvariantRule<CrossManifestData> = {
  id: "CROSS_VERSION_SATISFIES",
  section: "§7.2",
  description: "version_or_range in graph must be satisfiable by Brick's version",
  target: "cross",
  evaluate: (data) => {
    const failures: ValidationResult[] = [];

    for (const node of data.graph.nodes) {
      const brick = data.bricks.get(node.brick.brick_id);
      if (!brick) continue;

      const result = semverSatisfies(brick.version, node.brick.version_or_range);
      if (result === false) {
        failures.push(fail(CROSS_VERSION_SATISFIES,
          `node "${node.node_id}" requires "${node.brick.version_or_range}" ` +
          `but brick "${node.brick.brick_id}" is version "${brick.version}"`));
      } else if (result === null) {
        failures.push({
          rule: CROSS_VERSION_SATISFIES.id,
          section: CROSS_VERSION_SATISFIES.section,
          description: CROSS_VERSION_SATISFIES.description,
          status: "fail",
          severity: "warning",
          message: `node "${node.node_id}" version range "${node.brick.version_or_range}" ` +
            `has unrecognised format (cannot verify against brick "${node.brick.brick_id}")`,
        });
      }
    }
    return failures.length > 0 ? failures : pass(CROSS_VERSION_SATISFIES);
  },
};

// ── Export ───────────────────────────────────────────────────────────────────

export const crossInvariants: InvariantRule<CrossManifestData>[] = [
  CROSS_BRICK_MANIFEST_AVAILABLE,
  CROSS_SLOTS_FULLY_BOUND,
  CROSS_SLOT_TYPES_MATCH,
  CROSS_VERSION_SATISFIES,
];
