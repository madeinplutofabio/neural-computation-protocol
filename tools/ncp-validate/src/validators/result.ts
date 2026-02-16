// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

/**
 * Result Model structural validator (§9).
 * Validates against schemas/result.schema.json using AJV 2020-12.
 * No cross-field invariant rules for results.
 */

import type { Result, ValidationSummary } from "../types.js";
import {
  getAjvValidator,
  ajvErrorsToResults,
  buildSummary,
} from "./schema-helpers.js";

const SCHEMA_FILE = "result.schema.json";
const SECTION = "§9";

export function validateResult(
  result: Result,
  filePath: string,
): ValidationSummary {
  const validate = getAjvValidator(SCHEMA_FILE);
  const schemaValid = validate(result) as boolean;

  const schemaResults = schemaValid
    ? []
    : ajvErrorsToResults(validate.errors, SECTION);

  return buildSummary(filePath, schemaValid, schemaResults, []);
}
