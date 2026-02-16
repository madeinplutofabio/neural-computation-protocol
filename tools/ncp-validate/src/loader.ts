// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

/**
 * File loading utilities for NCP manifests and schemas.
 * Handles YAML and JSON loading with relative path resolution.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import YAML from "yaml";

/**
 * Load and parse a YAML or JSON file.
 * File type is determined by extension (.yaml/.yml → YAML, otherwise JSON).
 */
export function loadFile<T = unknown>(filePath: string): T {
  const absolutePath = path.resolve(filePath);

  if (!fs.existsSync(absolutePath)) {
    throw new Error(`File not found: ${absolutePath}`);
  }

  const content = fs.readFileSync(absolutePath, "utf-8");
  const ext = path.extname(absolutePath).toLowerCase();

  try {
    if (ext === ".yaml" || ext === ".yml") {
      return YAML.parse(content, { uniqueKeys: true }) as T;
    }
    return JSON.parse(content) as T;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new Error(`Failed to parse ${absolutePath}: ${msg}`);
  }
}

/**
 * Load a JSON Schema file from the repo root schemas/ directory.
 * NOTE: If you publish this package, include schemas/ in the npm files
 * or change this to resolve relative to the package dir instead of repo root.
 */
export function loadSchema(schemaFileName: string): Record<string, unknown> {
  // prevent path traversal
  const safeName = path.basename(schemaFileName);
  if (safeName !== schemaFileName) {
    throw new Error(`Invalid schema filename: ${schemaFileName}`);
  }

  const repoRoot = findRepoRoot();
  const schemaPath = path.join(repoRoot, "schemas", safeName);
  return loadFile<Record<string, unknown>>(schemaPath);
}

/**
 * Resolve a path relative to a manifest file's directory.
 * If the path is a URI (scheme:...), return it unchanged.
 */
export function resolveRelativeTo(
  basePath: string,
  relativePath: string,
): string {
  // URI / scheme detection (http:, https:, file:, etc.)
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(relativePath)) {
    return relativePath;
  }
  const baseDir = path.dirname(path.resolve(basePath));
  return path.resolve(baseDir, relativePath);
}

/**
 * Find the repository root by walking up from the current file
 * and looking for the schemas/ directory.
 */
function findRepoRoot(): string {
  let dir = path.dirname(fileURLToPath(import.meta.url));

  // Walk up until we find schemas/ directory
  for (let i = 0; i < 25; i++) {
    const candidate = path.join(dir, "schemas");
    if (fs.existsSync(candidate) && fs.statSync(candidate).isDirectory()) {
      return dir;
    }
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }

  throw new Error(
    "Could not find repository root (looking for schemas/ directory). " +
      "Ensure ncp-validate is run from within the NCP repository.",
  );
}
