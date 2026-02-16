// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

/**
 * Invocation Envelope structural validator (§8).
 * Validates against schemas/invocation-envelope.schema.json using AJV 2020-12.
 * No cross-field invariant rules for envelopes.
 */

import type { InvocationEnvelope, ValidationSummary } from "../types.js";
import {
  getAjvValidator,
  ajvErrorsToResults,
  buildSummary,
} from "./schema-helpers.js";

const SCHEMA_FILE = "invocation-envelope.schema.json";
const SECTION = "§8";

export function validateInvocationEnvelope(
  envelope: InvocationEnvelope,
  filePath: string,
): ValidationSummary {
  const validate = getAjvValidator(SCHEMA_FILE);
  const schemaValid = validate(envelope) as boolean;

  const schemaResults = schemaValid
    ? []
    : ajvErrorsToResults(validate.errors, SECTION);

  return buildSummary(filePath, schemaValid, schemaResults, []);
}
