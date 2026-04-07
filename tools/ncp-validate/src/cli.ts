#!/usr/bin/env node
// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

/**
 * ncp-validate CLI.
 * Validates NCP Brick and Graph manifests against JSON Schema
 * and cross-field invariant rules.
 */

import { Command } from "commander";
import chalk from "chalk";
import { loadFile } from "./loader.js";
import { validateBrickManifest } from "./validators/brick-manifest.js";
import { validateGraphManifest } from "./validators/graph-manifest.js";
import { validateCrossManifests } from "./validators/cross.js";
import { brickInvariants } from "./invariants/brick-invariants.js";
import { graphInvariants } from "./invariants/graph-invariants.js";
import { crossInvariants } from "./invariants/cross-invariants.js";
import type { ValidationSummary, BrickManifest, GraphManifest } from "./types.js";

import fs from "node:fs";
import path from "node:path";

// ── Exit codes ──────────────────────────────────────────────────────────────

const EXIT_OK = 0;
const EXIT_FAIL = 1;
const EXIT_ERROR = 2;

// ── Output formatting ───────────────────────────────────────────────────────

type OutputFormat = "human" | "json";

function formatHuman(summary: ValidationSummary): string {
  const lines: string[] = [];

  // Header
  lines.push(`Validating: ${summary.file}`);
  lines.push("");

  // Schema result
  if (summary.schema_valid) {
    lines.push(`Schema validation: ${chalk.green("✓ passed")}`);
  } else {
    lines.push(`Schema validation: ${chalk.red("✗ failed")}`);
    for (const r of summary.results) {
      if (r.rule.startsWith("schema:")) {
        lines.push(`  ${chalk.red("✗")} ${r.message ?? r.description}`);
      }
    }
  }
  lines.push("");

  // Invariant results
  const invariants = summary.results.filter((r) => !r.rule.startsWith("schema:"));
  if (invariants.length > 0) {
    lines.push("Cross-field invariants:");
    for (const r of invariants) {
      const icon =
        r.status === "pass"
          ? chalk.green("✓")
          : r.severity === "warning"
            ? chalk.yellow("⚠")
            : chalk.red("✗");
      const id = r.rule.padEnd(40);
      const section = chalk.dim(`[${r.section}]`);
      const desc = r.description;
      const line = `  ${icon} ${id} ${section}  ${desc}`;
      lines.push(line);
      if (r.status === "fail" && r.message) {
        lines.push(`    ${chalk.dim("→")} ${r.message}`);
      }
    }
    lines.push("");
  }

  // Summary line
  const { total, passed, failed, warnings } = summary.summary;
  const parts = [`${total} rules checked:`, `${passed} passed,`, `${failed} failed`];
  if (warnings > 0) parts.push(`(${warnings} warnings)`);
  const summaryIcon = summary.valid ? chalk.green("✓") : chalk.red("✗");
  lines.push(`${parts.join(" ")} ${summaryIcon}`);

  return lines.join("\n");
}

// ── Shared helpers ──────────────────────────────────────────────────────────

function output(summary: ValidationSummary, format: OutputFormat): void {
  if (format === "json") {
    console.log(JSON.stringify(summary, null, 2));
  } else {
    console.log(formatHuman(summary));
  }
  process.exit(summary.valid ? EXIT_OK : EXIT_FAIL);
}

function outputMultiple(summaries: ValidationSummary[], format: OutputFormat): void {
  const anyInvalid = summaries.some((s) => !s.valid);

  if (format === "json") {
    console.log(JSON.stringify(summaries, null, 2));
  } else {
    for (const summary of summaries) {
      console.log(formatHuman(summary));
      console.log("");
    }
  }

  process.exit(anyInvalid ? EXIT_FAIL : EXIT_OK);
}

function handleError(err: unknown, filePath: string): never {
  const msg = err instanceof Error ? err.message : String(err);
  console.error(chalk.red(`Error processing ${filePath}: ${msg}`));
  process.exit(EXIT_ERROR);
}

/**
 * Find Brick manifest files in a directory (one level deep).
 * Looks for manifest.yaml or manifest.yml in each immediate subdirectory.
 */
function findBrickManifests(brickDir: string): { path: string; data: BrickManifest }[] {
  const resolved = path.resolve(brickDir);
  if (!fs.existsSync(resolved)) {
    console.error(chalk.red(`Error: brick directory not found: ${resolved}`));
    process.exit(EXIT_ERROR);
  }

  const results: { path: string; data: BrickManifest }[] = [];
  for (const entry of fs.readdirSync(resolved, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    for (const filename of ["manifest.yaml", "manifest.yml"]) {
      const manifestFile = path.join(resolved, entry.name, filename);
      if (fs.existsSync(manifestFile)) {
        const data = loadFile<BrickManifest>(manifestFile);
        results.push({ path: manifestFile, data });
        break; // prefer .yaml over .yml
      }
    }
  }
  return results;
}

function validateFormat(format: string): OutputFormat {
  if (format !== "human" && format !== "json") {
    console.error(chalk.red(`Invalid format: "${format}". Must be "human" or "json".`));
    process.exit(EXIT_ERROR);
  }
  return format;
}

// ── CLI setup ───────────────────────────────────────────────────────────────

const program = new Command();

program
  .name("ncp-validate")
  .description("Validate NCP Brick and Graph manifests")
  .version("0.1.0");

// ── brick command ───────────────────────────────────────────────────────────

program
  .command("brick <manifest>")
  .description("Validate a Brick manifest (schema + invariants)")
  .option("-f, --format <format>", "Output format", "human")
  .addHelpText("after", "\nFormat choices: human, json")
  .action((manifestPath: string, opts: { format: string }) => {
    try {
      const format = validateFormat(opts.format);
      const data = loadFile<BrickManifest>(manifestPath);
      const summary = validateBrickManifest(data, manifestPath);
      output(summary, format);
    } catch (err) {
      handleError(err, manifestPath);
    }
  });

// ── graph command ───────────────────────────────────────────────────────────

program
  .command("graph <manifest>")
  .description("Validate a Graph manifest (schema + invariants)")
  .option("-f, --format <format>", "Output format", "human")
  .addHelpText("after", "\nFormat choices: human, json")
  .action((manifestPath: string, opts: { format: string }) => {
    try {
      const format = validateFormat(opts.format);
      const data = loadFile<GraphManifest>(manifestPath);
      const summary = validateGraphManifest(data, manifestPath);
      output(summary, format);
    } catch (err) {
      handleError(err, manifestPath);
    }
  });

// ── cross command ───────────────────────────────────────────────────────────

program
  .command("cross <graph-manifest>")
  .description(
    "Cross-validate a Graph against its Brick manifests.\n" +
    "Scans --brick-dir one level deep for subdirectories containing manifest.yaml or manifest.yml.",
  )
  .requiredOption("--brick-dir <directory>", "Directory containing Brick subdirectories")
  .option("-f, --format <format>", "Output format", "human")
  .addHelpText("after", "\nFormat choices: human, json")
  .action(
    (
      graphPath: string,
      opts: { brickDir: string; format: string },
    ) => {
      try {
        const format = validateFormat(opts.format);

        // Phase 1: Validate graph schema + invariants
        const graphData = loadFile<GraphManifest>(graphPath);
        const graphSummary = validateGraphManifest(graphData, graphPath);
        if (!graphSummary.schema_valid) {
          // Graph schema fails → report and exit
          output(graphSummary, format);
        }

        // Phase 2: Load and validate all Brick manifests
        const brickFiles = findBrickManifests(opts.brickDir);
        const invalidBrickSummaries: ValidationSummary[] = [];
        const validBricks = new Map<string, BrickManifest>();
        let anyBrickSchemaFailed = false;

        for (const { path: brickPath, data: brickData } of brickFiles) {
          const brickSummary = validateBrickManifest(brickData, brickPath);

          if (!brickSummary.schema_valid) {
            anyBrickSchemaFailed = true;
            invalidBrickSummaries.push(brickSummary);
            continue;
          }

          // Schema ok → usable for cross rules
          validBricks.set(brickData.brick_id, brickData);

          if (!brickSummary.valid) {
            invalidBrickSummaries.push(brickSummary);
          }
        }

        if (anyBrickSchemaFailed) {
          // Some Brick schemas failed → can't run cross; report all and exit
          outputMultiple([graphSummary, ...invalidBrickSummaries], format);
        }

        // Phase 3: Run cross-manifest invariants
        const crossSummary = validateCrossManifests(
          { graph: graphData, bricks: validBricks },
          graphPath,
        );

        // Include invalid brick summaries so exit code reflects them
        outputMultiple(
          [graphSummary, ...invalidBrickSummaries, crossSummary],
          format,
        );
      } catch (err) {
        handleError(err, graphPath);
      }
    },
  );

// ── pack command ────────────────────────────────────────────────────────────

program
  .command("pack")
  .description(
    "Stamp a Brick manifest template with real artifact digest and size.\n" +
    "Reads WASM file, computes sha256, and writes completed manifest.",
  )
  .requiredOption("--wasm <path>", "Path to WASM artifact")
  .requiredOption("--template <path>", "Path to manifest YAML template")
  .requiredOption("--output <path>", "Output path for stamped manifest")
  .action(
    async (opts: { wasm: string; template: string; output: string }) => {
      try {
        const crypto = await import("node:crypto");
        const YAML = await import("yaml");
        const wasmPath = path.resolve(opts.wasm);
        const templatePath = path.resolve(opts.template);
        const outputPath = path.resolve(opts.output);

        if (!fs.existsSync(wasmPath)) {
          console.error(chalk.red(`WASM file not found: ${wasmPath}`));
          process.exit(EXIT_ERROR);
        }
        if (!fs.existsSync(templatePath)) {
          console.error(chalk.red(`Template not found: ${templatePath}`));
          process.exit(EXIT_ERROR);
        }

        // Read WASM and compute digest
        const wasmBytes = fs.readFileSync(wasmPath);
        const hash = crypto.createHash("sha256").update(wasmBytes).digest("hex");
        const digest = `sha256:${hash}`;
        const sizeBytes = wasmBytes.length;

        // Load template — enforce JSON data model per Section 5.2
        const templateContent = fs.readFileSync(templatePath, "utf-8");

        // AST check: reject anchors, aliases, and non-JSON tags
        const doc = YAML.parseDocument(templateContent);
        YAML.visit(doc, {
          Pair(_key: unknown, pair: unknown) {
            const p = pair as { key?: unknown; value?: unknown };
            for (const node of [p.key, p.value]) {
              if (node && typeof node === "object") {
                const n = node as Record<string, unknown>;
                if (n.anchor) {
                  console.error(chalk.red(
                    `Template contains YAML anchor "&${n.anchor}", rejected per Section 5.2 data model constraint.`,
                  ));
                  process.exit(EXIT_ERROR);
                }
                if (n.tag) {
                  console.error(chalk.red(
                    `Template contains YAML tag "${n.tag}", rejected per Section 5.2 data model constraint.`,
                  ));
                  process.exit(EXIT_ERROR);
                }
              }
            }
          },
          Alias() {
            console.error(chalk.red(
              "Template contains YAML alias, rejected per Section 5.2 data model constraint.",
            ));
            process.exit(EXIT_ERROR);
          },
        });

        // Parse with JSON schema — prevents implicit typing, merge keys, aliases
        const manifest = YAML.parse(templateContent, {
          schema: "json",
          version: "1.2",
          uniqueKeys: true,
          merge: false,
          stringKeys: true,
          strict: true,
          resolveKnownTags: false,
          maxAliasCount: 0,
        });

        if (!manifest || typeof manifest !== "object") {
          console.error(chalk.red("Template did not parse to an object."));
          process.exit(EXIT_ERROR);
        }

        // Stamp artifact fields
        if (!manifest.artifact || typeof manifest.artifact !== "object") {
          manifest.artifact = {};
        }
        manifest.artifact.digest = digest;
        manifest.artifact.size_bytes = sizeBytes;

        // Write output (ensure directory exists)
        fs.mkdirSync(path.dirname(outputPath), { recursive: true });
        const outputContent = YAML.stringify(manifest, {
          lineWidth: 0,
          singleQuote: false,
        });
        fs.writeFileSync(outputPath, outputContent, "utf-8");

        console.log(`Stamped manifest written to ${outputPath}`);
        console.log(`  artifact.digest:     ${digest}`);
        console.log(`  artifact.size_bytes: ${sizeBytes}`);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.error(chalk.red(`Error: ${msg}`));
        process.exit(EXIT_ERROR);
      }
    },
  );

// ── rules command ───────────────────────────────────────────────────────────

program
  .command("rules")
  .description("List all validation rules")
  .option("-t, --target <target>", "Filter by target: brick, graph, or cross")
  .action((opts: { target?: string }) => {
    const allRules = [
      ...brickInvariants,
      ...graphInvariants,
      ...crossInvariants,
    ];

    if (opts.target && !["brick", "graph", "cross"].includes(opts.target)) {
      console.error(chalk.red(`Invalid target: "${opts.target}". Must be brick, graph, or cross.`));
      process.exit(EXIT_ERROR);
    }

    const filtered = opts.target
      ? allRules.filter((r) => r.target === opts.target)
      : allRules;

    console.log(`\n${filtered.length} rules${opts.target ? ` (target: ${opts.target})` : ""}:\n`);

    for (const rule of filtered) {
      const severity = rule.severity === "warning" ? chalk.yellow(" [warn]") : "";
      console.log(
        `  ${chalk.bold(rule.id.padEnd(42))} ${chalk.dim(`[${rule.section}]`)}  ${rule.description}${severity}`,
      );
    }
    console.log("");
  });

// ── Run ─────────────────────────────────────────────────────────────────────

program.parse();
