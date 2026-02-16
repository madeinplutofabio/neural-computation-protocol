// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

/**
 * Shared AJV helpers for all structural validators.
 * Centralises Draft 2020-12 instantiation, schema compilation,
 * and AJV-error → ValidationResult conversion.
 */

import _Ajv2020 from "ajv/dist/2020.js";
import _addFormats from "ajv-formats";
import type { ValidateFunction, ErrorObject } from "ajv/dist/2020.js";
import { loadSchema } from "../loader.js";
import type { ValidationResult } from "../types.js";

// CJS default-export interop for NodeNext module resolution
const Ajv2020 = _Ajv2020 as unknown as typeof _Ajv2020.default;
const addFormats = _addFormats as unknown as typeof _addFormats.default;

// ── Singleton AJV instance ──────────────────────────────────────────────────

let ajv: InstanceType<typeof Ajv2020> | null = null;

function getAjv(): InstanceType<typeof Ajv2020> {
  if (!ajv) {
    ajv = new Ajv2020({ allErrors: true });
    addFormats(ajv);
  }
  return ajv;
}

// ── Compile cache (keyed by filename for deterministic lookup) ───────────────

const compiledByFile = new Map<string, ValidateFunction>();

// ── Public helpers ──────────────────────────────────────────────────────────

/**
 * Compile (or retrieve from cache) the AJV validate function for a schema.
 * The schema file is loaded from the repo-root `schemas/` directory.
 * Caching is keyed on schemaFileName so it works even if a schema lacks $id.
 */
export function getAjvValidator(schemaFileName: string): ValidateFunction {
  const cached = compiledByFile.get(schemaFileName);
  if (cached) return cached;

  const instance = getAjv();
  const schema = loadSchema(schemaFileName);
  const schemaId = schema.$id as string | undefined;

  // Check AJV's internal cache by $id first
  if (schemaId) {
    const existing = instance.getSchema(schemaId);
    if (existing) {
      compiledByFile.set(schemaFileName, existing);
      return existing;
    }
  }

  const compiled = instance.compile(schema);
  compiledByFile.set(schemaFileName, compiled);
  return compiled;
}

/**
 * Convert AJV error objects into ValidationResult items.
 * Includes keyword + schemaPath for easier debugging.
 */
export function ajvErrorsToResults(
  errors: ErrorObject[] | null | undefined,
  section: string,
): ValidationResult[] {
  if (!errors || errors.length === 0) return [];

  return errors.map((err) => ({
    rule: `schema:${err.keyword}`,
    section,
    description: `Schema validation error at ${err.instancePath || "/"}`,
    message: `${err.instancePath || "/"}: ${err.message ?? "unknown"} (keyword: ${err.keyword}, schemaPath: ${err.schemaPath})`,
    status: "fail" as const,
    severity: "error" as const,
  }));
}

/**
 * Determine overall validity: only severity:"error" results count.
 * Warning-only failures leave the manifest valid.
 */
export function isValid(results: ValidationResult[]): boolean {
  return !results.some((r) => r.status === "fail" && r.severity === "error");
}
