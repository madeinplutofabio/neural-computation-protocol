// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

/**
 * Rule registry framework for cross-field invariant validation.
 * Each invariant module exports an array of InvariantRule objects.
 * The registry collects them for CLI listing and selective execution.
 */

import type { ValidationResult, RuleSeverity } from "./types.js";

// ── Rule definition ─────────────────────────────────────────────────────────

export type RuleTarget = "brick" | "graph" | "cross";

export type RuleId =
  | `BRICK_${string}`
  | `GRAPH_${string}`
  | `CROSS_${string}`;

/** Type-erased rule metadata (no generic — safe for pass/fail helpers). */
export interface RuleMeta {
  readonly id: RuleId;
  readonly section: string;
  readonly description: string;
  readonly target: RuleTarget;
  readonly severity?: RuleSeverity;
}

export interface InvariantRule<T = unknown> extends RuleMeta {
  /**
   * Evaluate the rule against a parsed manifest (or pair of manifests).
   * May return a single result or multiple (e.g. one per offending item).
   */
  evaluate: (data: T) => ValidationResult | ValidationResult[];
}

// ── Helpers ─────────────────────────────────────────────────────────────────

/** Create a passing result for the given rule. */
export function pass(rule: RuleMeta): ValidationResult {
  return {
    rule: rule.id,
    section: rule.section,
    description: rule.description,
    status: "pass",
    severity: rule.severity ?? "error",
  };
}

/** Create a failing result for the given rule with an explanatory message. */
export function fail(rule: RuleMeta, message: string): ValidationResult {
  return {
    rule: rule.id,
    section: rule.section,
    description: rule.description,
    status: "fail",
    severity: rule.severity ?? "error",
    message,
  };
}

/**
 * Run a set of invariant rules against data, returning all results.
 * Rules may return a single result or an array of results.
 * Fail-safe: if a rule throws, it produces a fail result instead of crashing.
 */
export function runRules<T>(
  rules: InvariantRule<T>[],
  data: T,
): ValidationResult[] {
  return rules.flatMap((rule) => {
    try {
      const out = rule.evaluate(data);
      return Array.isArray(out) ? out : [out];
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return [fail(rule, `[${rule.id}] Rule threw: ${msg}`)];
    }
  });
}
