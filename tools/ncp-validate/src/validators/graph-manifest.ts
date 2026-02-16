// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

/**
 * Graph Manifest structural + invariant validator (§7).
 * Validates against schemas/graph-manifest.schema.json using AJV 2020-12,
 * then runs cross-field invariant rules if schema passes.
 */

import type { GraphManifest, ValidationSummary } from "../types.js";
import {
  getAjvValidator,
  ajvErrorsToResults,
  buildSummary,
} from "./schema-helpers.js";
import { runRules } from "../rules.js";
import { graphInvariants } from "../invariants/graph-invariants.js";

const SCHEMA_FILE = "graph-manifest.schema.json";
const SECTION = "§7";

export function validateGraphManifest(
  manifest: GraphManifest,
  filePath: string,
): ValidationSummary {
  const validate = getAjvValidator(SCHEMA_FILE);
  const schemaValid = validate(manifest) as boolean;

  const schemaResults = schemaValid
    ? []
    : ajvErrorsToResults(validate.errors, SECTION);

  // Only run invariants if schema passes
  const invariantResults = schemaValid
    ? runRules(graphInvariants, manifest)
    : [];

  return buildSummary(filePath, schemaValid, schemaResults, invariantResults);
}
