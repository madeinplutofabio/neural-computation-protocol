// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

/**
 * Brick Manifest structural + invariant validator (§6).
 * Validates against schemas/brick-manifest.schema.json using AJV 2020-12,
 * then runs cross-field invariant rules if schema passes.
 */

import type { BrickManifest, ValidationSummary } from "../types.js";
import {
  getAjvValidator,
  ajvErrorsToResults,
  buildSummary,
} from "./schema-helpers.js";
import { runRules } from "../rules.js";
import { brickInvariants } from "../invariants/brick-invariants.js";

const SCHEMA_FILE = "brick-manifest.schema.json";
const SECTION = "§6";

export function validateBrickManifest(
  manifest: BrickManifest,
  filePath: string,
): ValidationSummary {
  const validate = getAjvValidator(SCHEMA_FILE);
  const schemaValid = validate(manifest) as boolean;

  const schemaResults = schemaValid
    ? []
    : ajvErrorsToResults(validate.errors, SECTION);

  // Only run invariants if schema passes (avoids null/undefined surprises)
  const invariantResults = schemaValid
    ? runRules(brickInvariants, manifest)
    : [];

  return buildSummary(filePath, schemaValid, schemaResults, invariantResults);
}
