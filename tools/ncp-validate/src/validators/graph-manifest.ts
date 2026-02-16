// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

/**
 * Graph Manifest structural validator (§7).
 * Validates against schemas/graph-manifest.schema.json using AJV 2020-12.
 */

import type { GraphManifest, ValidationSummary } from "../types.js";
import {
  getAjvValidator,
  ajvErrorsToResults,
  isValid,
} from "./schema-helpers.js";

const SCHEMA_FILE = "graph-manifest.schema.json";
const SECTION = "§7 Graph Manifest";

export function validateGraphManifest(
  manifest: GraphManifest,
  filePath: string,
): ValidationSummary {
  const validate = getAjvValidator(SCHEMA_FILE);
  const schemaValid = validate(manifest) as boolean;

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
