// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

/**
 * Result Model structural validator (§9).
 * Validates against schemas/result.schema.json using AJV 2020-12.
 */

import type { Result, ValidationSummary } from "../types.js";
import {
  getAjvValidator,
  ajvErrorsToResults,
  isValid,
} from "./schema-helpers.js";

const SCHEMA_FILE = "result.schema.json";
const SECTION = "§9 Result Model";

export function validateResult(
  result: Result,
  filePath: string,
): ValidationSummary {
  const validate = getAjvValidator(SCHEMA_FILE);
  const schemaValid = validate(result) as boolean;

  const results = schemaValid
    ? []
    : ajvErrorsToResults(validate.errors, SECTION);

  return {
    file: filePath,
    valid: schemaValid && isValid(results),
    schema_valid: schemaValid,
    results,
    summary: {
      total: results.length,
      passed: results.filter((r) => r.status === "pass").length,
      failed: results.filter((r) => r.status === "fail").length,
    },
  };
}
