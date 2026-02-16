// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

/**
 * Invocation Envelope structural validator (§8).
 * Validates against schemas/invocation-envelope.schema.json using AJV 2020-12.
 */

import type { InvocationEnvelope, ValidationSummary } from "../types.js";
import {
  getAjvValidator,
  ajvErrorsToResults,
  isValid,
} from "./schema-helpers.js";

const SCHEMA_FILE = "invocation-envelope.schema.json";
const SECTION = "§8 Invocation Envelope";

export function validateEnvelope(
  envelope: InvocationEnvelope,
  filePath: string,
): ValidationSummary {
  const validate = getAjvValidator(SCHEMA_FILE);
  const schemaValid = validate(envelope) as boolean;

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
