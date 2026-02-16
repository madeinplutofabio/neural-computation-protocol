// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

/**
 * Graph Manifest cross-field invariant rules (§7).
 * These complement the JSON Schema structural validation with
 * referential integrity and consistency checks.
 *
 * These rules are only run when schema validation passes.
 */

import type { GraphManifest, ValidationResult, ErrorClass } from "../types.js";
import type { InvariantRule } from "../rules.js";
import { pass, fail } from "../rules.js";

// ── Constants ───────────────────────────────────────────────────────────────

// TODO: centralise in types.ts if more modules need this list
const ERROR_CLASSES: ErrorClass[] = [
  "INVALID_INPUT",
  "COMPUTATION_ERROR",
  "RESOURCE_EXCEEDED",
  "RUNTIME_REJECTED",
  "LOW_CONFIDENCE",
];

// ── Helpers ─────────────────────────────────────────────────────────────────

/** Collect all node_ids into a Set for O(1) lookups. */
function nodeIdSet(g: GraphManifest): Set<string> {
  return new Set(g.nodes.map((n) => n.node_id));
}

// ── Rule definitions ────────────────────────────────────────────────────────

const GRAPH_NODE_IDS_UNIQUE: InvariantRule<GraphManifest> = {
  id: "GRAPH_NODE_IDS_UNIQUE",
  section: "§7.2",
  description: "all node_ids must be unique",
  target: "graph",
  evaluate: (g) => {
    const seen = new Set<string>();
    const dupes = new Set<string>();
    for (const node of g.nodes) {
      if (seen.has(node.node_id)) dupes.add(node.node_id);
      seen.add(node.node_id);
    }
    if (dupes.size > 0) {
      return [...dupes].map((id) =>
        fail(GRAPH_NODE_IDS_UNIQUE, `duplicate node_id: "${id}"`)
      );
    }
    return pass(GRAPH_NODE_IDS_UNIQUE);
  },
};

const GRAPH_EDGE_IDS_UNIQUE: InvariantRule<GraphManifest> = {
  id: "GRAPH_EDGE_IDS_UNIQUE",
  section: "§7.3",
  description: "all edge_ids must be unique",
  target: "graph",
  evaluate: (g) => {
    const seen = new Set<string>();
    const dupes = new Set<string>();
    for (const edge of g.edges) {
      if (seen.has(edge.edge_id)) dupes.add(edge.edge_id);
      seen.add(edge.edge_id);
    }
    if (dupes.size > 0) {
      return [...dupes].map((id) =>
        fail(GRAPH_EDGE_IDS_UNIQUE, `duplicate edge_id: "${id}"`)
      );
    }
    return pass(GRAPH_EDGE_IDS_UNIQUE);
  },
};

const GRAPH_EDGE_SOURCE_EXISTS: InvariantRule<GraphManifest> = {
  id: "GRAPH_EDGE_SOURCE_EXISTS",
  section: "§7.3",
  description: "every edge.source_node must reference an existing node_id",
  target: "graph",
  evaluate: (g) => {
    const nodes = nodeIdSet(g);
    const failures: ValidationResult[] = [];
    for (const edge of g.edges) {
      if (!nodes.has(edge.source_node)) {
        failures.push(fail(GRAPH_EDGE_SOURCE_EXISTS,
          `edge "${edge.edge_id}" references unknown source_node "${edge.source_node}"`));
      }
    }
    return failures.length > 0 ? failures : pass(GRAPH_EDGE_SOURCE_EXISTS);
  },
};

const GRAPH_EDGE_TARGET_EXISTS: InvariantRule<GraphManifest> = {
  id: "GRAPH_EDGE_TARGET_EXISTS",
  section: "§7.3",
  description: "every edge.target_node must reference an existing node_id",
  target: "graph",
  evaluate: (g) => {
    const nodes = nodeIdSet(g);
    const failures: ValidationResult[] = [];
    for (const edge of g.edges) {
      if (!nodes.has(edge.target_node)) {
        failures.push(fail(GRAPH_EDGE_TARGET_EXISTS,
          `edge "${edge.edge_id}" references unknown target_node "${edge.target_node}"`));
      }
    }
    return failures.length > 0 ? failures : pass(GRAPH_EDGE_TARGET_EXISTS);
  },
};

const GRAPH_NO_SELF_EDGES: InvariantRule<GraphManifest> = {
  id: "GRAPH_NO_SELF_EDGES",
  section: "§7.3",
  description: "source_node ≠ target_node for every edge",
  target: "graph",
  evaluate: (g) => {
    const failures: ValidationResult[] = [];
    for (const edge of g.edges) {
      if (edge.source_node === edge.target_node) {
        failures.push(fail(GRAPH_NO_SELF_EDGES,
          `edge "${edge.edge_id}" is a self-loop (source and target are both "${edge.source_node}")`));
      }
    }
    return failures.length > 0 ? failures : pass(GRAPH_NO_SELF_EDGES);
  },
};

const GRAPH_ON_ERROR_TARGETS_EXIST: InvariantRule<GraphManifest> = {
  id: "GRAPH_ON_ERROR_TARGETS_EXIST",
  section: "§7.4",
  description: "every on_error target must reference an existing node_id",
  target: "graph",
  evaluate: (g) => {
    const nodes = nodeIdSet(g);
    const failures: ValidationResult[] = [];

    for (const edge of g.edges) {
      const onError = edge.on_error;
      if (!onError) continue;

      for (const cls of ERROR_CLASSES) {
        const targets = (onError as Record<string, unknown>)[cls];
        if (targets == null) continue;

        if (!Array.isArray(targets)) {
          failures.push(fail(GRAPH_ON_ERROR_TARGETS_EXIST,
            `edge "${edge.edge_id}" on_error.${cls} must be an array of node_ids`));
          continue;
        }

        for (const target of targets) {
          if (typeof target !== "string") {
            failures.push(fail(GRAPH_ON_ERROR_TARGETS_EXIST,
              `edge "${edge.edge_id}" on_error.${cls} contains non-string target`));
            continue;
          }
          if (!nodes.has(target)) {
            failures.push(fail(GRAPH_ON_ERROR_TARGETS_EXIST,
              `edge "${edge.edge_id}" on_error.${cls} references unknown node "${target}"`));
          }
        }
      }
    }
    return failures.length > 0 ? failures : pass(GRAPH_ON_ERROR_TARGETS_EXIST);
  },
};

const GRAPH_QUORUM_VALID: InvariantRule<GraphManifest> = {
  id: "GRAPH_QUORUM_VALID",
  section: "§7.0",
  description: "mode=quorum ⇒ 0 < quorum_n ≤ quorum_m",
  target: "graph",
  evaluate: (g) => {
    const failures: ValidationResult[] = [];
    for (const node of g.nodes) {
      const act = node.activation;
      if (!act || act.mode !== "quorum") continue;

      if (act.quorum_n == null || act.quorum_m == null) {
        failures.push(fail(GRAPH_QUORUM_VALID,
          `node "${node.node_id}" has mode=quorum but quorum_n or quorum_m is missing`));
        continue;
      }
      if (act.quorum_n <= 0) {
        failures.push(fail(GRAPH_QUORUM_VALID,
          `node "${node.node_id}" has quorum_n=${act.quorum_n} (must be >0)`));
      }
      if (act.quorum_n > act.quorum_m) {
        failures.push(fail(GRAPH_QUORUM_VALID,
          `node "${node.node_id}" has quorum_n=${act.quorum_n} > quorum_m=${act.quorum_m}`));
      }
    }
    return failures.length > 0 ? failures : pass(GRAPH_QUORUM_VALID);
  },
};

const GRAPH_PRIORITY_EDGES_HAVE_PRIORITY: InvariantRule<GraphManifest> = {
  id: "GRAPH_PRIORITY_EDGES_HAVE_PRIORITY",
  section: "§7.3",
  description: "conflict_resolution=priority ⇒ all inbound edges have priority field",
  target: "graph",
  evaluate: (g) => {
    // Build set of node_ids that use priority conflict resolution
    const priorityNodes = new Set<string>();
    for (const node of g.nodes) {
      if (node.activation?.conflict_resolution === "priority") {
        priorityNodes.add(node.node_id);
      }
    }
    if (priorityNodes.size === 0) return pass(GRAPH_PRIORITY_EDGES_HAVE_PRIORITY);

    // Check that all inbound edges to priority nodes have priority field
    const failures: ValidationResult[] = [];
    for (const edge of g.edges) {
      if (priorityNodes.has(edge.target_node) && edge.priority == null) {
        failures.push(fail(GRAPH_PRIORITY_EDGES_HAVE_PRIORITY,
          `edge "${edge.edge_id}" targets priority-resolution node "${edge.target_node}" but has no priority field`));
      }
    }
    return failures.length > 0 ? failures : pass(GRAPH_PRIORITY_EDGES_HAVE_PRIORITY);
  },
};

// ── Export ───────────────────────────────────────────────────────────────────

export const graphInvariants: InvariantRule<GraphManifest>[] = [
  GRAPH_NODE_IDS_UNIQUE,
  GRAPH_EDGE_IDS_UNIQUE,
  GRAPH_EDGE_SOURCE_EXISTS,
  GRAPH_EDGE_TARGET_EXISTS,
  GRAPH_NO_SELF_EDGES,
  GRAPH_ON_ERROR_TARGETS_EXIST,
  GRAPH_QUORUM_VALID,
  GRAPH_PRIORITY_EDGES_HAVE_PRIORITY,
];
