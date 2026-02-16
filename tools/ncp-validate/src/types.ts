// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

/**
 * TypeScript type definitions for all NCP v0.2.3 structures.
 * These types mirror the JSON Schema definitions in schemas/.
 */

// ─── Shared Enums (used across sections) ────────────────────────────────────

export type ErrorClass =
  | "INVALID_INPUT"
  | "COMPUTATION_ERROR"
  | "RESOURCE_EXCEEDED"
  | "RUNTIME_REJECTED"
  | "LOW_CONFIDENCE";

export type NonLowConfidenceErrorClass =
  | "INVALID_INPUT"
  | "COMPUTATION_ERROR"
  | "RESOURCE_EXCEEDED"
  | "RUNTIME_REJECTED";

// ─── Brick Manifest (§6) ────────────────────────────────────────────────────

export type DeterminismMode = "bit_exact" | "bounded_drift" | "stochastic";
export type TimeModel = "pure" | "logical" | "wall_bounded";
export type CarryStateClass = "none" | "tiny" | "medium" | "large";
export type CarryStateTransport = "inline" | "handle";
export type BrickStatus = "active" | "deprecated" | "sunset";
export type ArtifactFormat = "wasm";
export type ReproducibilityLevel = "seed_deterministic" | "seed_approximate";
export type RefConsistency = "PINNED" | "LIVE_OPS";

export interface BrickArtifact {
  format: ArtifactFormat;
  entrypoint: string;
  digest: string;
  size_bytes: number;
}

export interface BrickSchemas {
  input: string;
  output: string;
  carry_state: string | null;
  metadata?: string | null;
}

export interface BrickLimits {
  max_ms: number;
  max_mem_mb: number;
  max_output_bytes: number;
  max_input_bytes?: number;
  estimated_cost_per_invoke_usd?: number;
}

export interface Determinism {
  mode: DeterminismMode;
  epsilon?: number;
  seed_source?: "ctx.seed";
  model_pin?: string;
  reproducibility_level?: ReproducibilityLevel;
}

export interface GraphRefSlot {
  slot: string;
  type: string;
  access: "read";
  ref_consistency: RefConsistency;
}

export interface BrickManifest {
  brick_id: string;
  version: string;
  ncp_protocol_range: string;
  required_runtime_features: string[];
  status: BrickStatus;
  successor?: string;
  artifact: BrickArtifact;
  schemas: BrickSchemas;
  limits: BrickLimits;
  capabilities: never[];
  determinism: Determinism;
  time_model: TimeModel;
  carry_state_class: CarryStateClass;
  carry_state_transport: CarryStateTransport;
  carry_state_max_bytes: number;
  carry_state_side_effects_schema?: string | null;
  confidence_threshold?: number;
  graph_ref_slots: GraphRefSlot[];
}

// ─── Graph Manifest (§7) ────────────────────────────────────────────────────

export type ActivationMode = "all_required" | "any" | "quorum";
export type ConflictResolution = "reject" | "priority" | "deterministic";
export type CarryStateInit = "zero" | "from_graph_defaults" | "from_first_input";
export type GraphVersionChange = "reset" | "migrate_if_compatible" | "reject_session";

export interface ActivationPolicy {
  mode: ActivationMode;
  timeout_ms?: number;
  allow_partial?: boolean;
  required_fields?: string[];
  defaults?: Record<string, unknown>;
  conflict_resolution?: ConflictResolution;
  quorum_n?: number;
  quorum_m?: number;
}

export interface CarryStateLifecycle {
  init?: CarryStateInit;
  ttl_seconds?: number;
  on_graph_version_change?: GraphVersionChange;
}

export interface BrickRef {
  brick_id: string;
  version_or_range: string;
}

export interface NodeBindings {
  graph_ref_slot_values: Record<string, unknown>;
}

export interface NodeDefinition {
  node_id: string;
  brick: BrickRef;
  bindings: NodeBindings;
  activation?: ActivationPolicy;
  carry_state_lifecycle?: CarryStateLifecycle;
}

export interface EdgeMapping {
  from: string;
  to: string;
}

export type OnErrorRouting = Partial<Record<ErrorClass, string[]>> &
  Record<string, unknown>;

export type OnSuccessRouting = Record<string, unknown>;

export interface EdgeDefinition {
  edge_id: string;
  source_node: string;
  target_node: string;
  mapping: EdgeMapping[];
  priority?: number;
  on_success?: OnSuccessRouting;
  on_error?: OnErrorRouting;
}

export interface GraphBudgets {
  max_nodes_per_tick?: number;
  max_total_ms_per_tick?: number;
  max_fan_out?: number;
  max_trace_bytes?: number;
}

export interface GraphManifest {
  graph_id: string;
  graph_version: string;
  synaptic_state_version: string;
  nodes: NodeDefinition[];
  edges: EdgeDefinition[];
  budgets?: GraphBudgets;
}

// ─── Invocation Envelope (§8) ───────────────────────────────────────────────

export type TriggerRoutingReason = "on_success" | `on_error:${ErrorClass}`;

export interface RootTrigger {
  source_node_id: "__root__";
  source_step: number;
  edge_id: "__root__";
  routing_reason: TriggerRoutingReason;
}

type NonRootId = Exclude<string, "__root__">;

export interface NonRootTrigger {
  source_node_id: NonRootId;
  source_step: number;
  edge_id: NonRootId;
  routing_reason: TriggerRoutingReason;
}

export type TriggerProvenance = RootTrigger | NonRootTrigger;

export interface InvocationContext {
  trace_id: string;
  session_id: string;
  step: number;
  graph_id: string;
  graph_version: string;
  trigger: TriggerProvenance;
  seed?: number;
  logical_time?: number;
  wall_time?: string;
}

interface InvocationEnvelopeBase {
  ctx: InvocationContext;
  input: unknown;
  graph_refs?: Record<string, unknown>;
}

/** Inline carry state transport (carry_state_transport=inline). */
type InlineCarryStateEnvelope = InvocationEnvelopeBase & {
  carry_state: unknown;
  carry_state_handle?: never;
  carry_state_working_set?: never;
};

/** Handle-based carry state transport (carry_state_transport=handle). */
type HandleCarryStateEnvelope = InvocationEnvelopeBase & {
  carry_state?: never;
  carry_state_handle: string;
  carry_state_working_set?: unknown;
};

/**
 * No carry state fields — valid when the Brick declares
 * carry_state_class=none (or when carry state is not applicable).
 */
type NoCarryStateEnvelope = InvocationEnvelopeBase & {
  carry_state?: never;
  carry_state_handle?: never;
  carry_state_working_set?: never;
};

export type InvocationEnvelope =
  | InlineCarryStateEnvelope
  | HandleCarryStateEnvelope
  | NoCarryStateEnvelope;

// ─── Result Model (§9) ──────────────────────────────────────────────────────

export type RetryAdvice = "NEVER" | "IMMEDIATE" | "BACKOFF";
export type Severity = "WARN" | "ERROR" | "FATAL";

export interface ErrorObject {
  error_class: ErrorClass;
  message: string;
  retry_advice: RetryAdvice;
  severity: Severity;
}

export type LowConfidenceError = ErrorObject & {
  error_class: "LOW_CONFIDENCE";
};

export type FailureError = ErrorObject & {
  error_class: NonLowConfidenceErrorClass;
};

export interface SuccessResult {
  type: "Success";
  output: unknown;
  error?: never;
  carry_state_side_effects?: never;
  carry_state_next?: unknown;
  obs_events?: unknown[];
  metadata?: unknown;
}

export interface LowConfidenceResult {
  type: "LowConfidence";
  output: { confidence: number; [key: string]: unknown };
  error: LowConfidenceError;
  carry_state_side_effects?: never;
  carry_state_next?: unknown;
  obs_events?: unknown[];
  metadata?: unknown;
}

export interface FailureResult {
  type: "Failure";
  output?: never;
  carry_state_next?: never;
  error: FailureError;
  carry_state_side_effects?: unknown;
  obs_events?: unknown[];
  metadata?: unknown;
}

export type Result = SuccessResult | LowConfidenceResult | FailureResult;

// ─── Validation Types ───────────────────────────────────────────────────────

export type RuleStatus = "pass" | "fail";
export type RuleSeverity = "error" | "warning";

export interface ValidationResult {
  rule: string;
  section: string;
  description: string;
  status: RuleStatus;
  message?: string;
  severity: RuleSeverity;
}

export interface ValidationSummary {
  file: string;
  valid: boolean;
  schema_valid: boolean;
  results: ValidationResult[];
  summary: {
    total: number;
    passed: number;
    failed: number;
  };
}
