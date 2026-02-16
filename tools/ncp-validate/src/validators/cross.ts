// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

/**
 * Cross-manifest validator (§6.10 / §7.2).
 * Runs cross-manifest invariant rules against a Graph + its Brick manifests.
 */

import type { ValidationSummary } from "../types.js";
import { buildSummary } from "./schema-helpers.js";
import { runRules } from "../rules.js";
import {
  crossInvariants,
  type CrossManifestData,
} from "../invariants/cross-invariants.js";

export type { CrossManifestData } from "../invariants/cross-invariants.js";

/**
 * Validate cross-manifest consistency between a Graph and its Bricks.
 * Assumes both Graph and Brick schema validation have already passed.
 */
export function validateCrossManifests(
  data: CrossManifestData,
  filePath?: string,
): ValidationSummary {
  const invariantResults = runRules(crossInvariants, data);

  return buildSummary(
    filePath ?? `cross:${data.graph.graph_id}`,
    true, // schema assumed valid (caller responsibility)
    [],
    invariantResults,
  );
}
