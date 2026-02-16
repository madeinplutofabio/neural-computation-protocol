// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 Fabio Marcello Salvadori

/**
 * Brick Manifest cross-field invariant rules (§6).
 * These complement the JSON Schema structural validation with
 * checks that cannot be expressed in JSON Schema alone.
 *
 * Note: Several of these invariants ARE also enforced in the schema
 * via if/then/else. They are re-checked here for defence in depth
 * and to produce rule-specific error messages.
 *
 * These rules are only run when schema validation passes (no
 * null/undefined surprises from malformed input).
 */

import type { BrickManifest } from "../types.js";
import type { InvariantRule } from "../rules.js";
import { pass, fail } from "../rules.js";

// ── Rule definitions ────────────────────────────────────────────────────────

const BRICK_CARRY_NULL_IFF_NONE: InvariantRule<BrickManifest> = {
  id: "BRICK_CARRY_NULL_IFF_NONE",
  section: "§6.8",
  description: "carry_state_class=none ⇔ schemas.carry_state=null",
  target: "brick",
  evaluate: (m) => {
    const isNone = m.carry_state_class === "none";
    const isNull = m.schemas.carry_state === null;
    if (isNone && !isNull) {
      return fail(BRICK_CARRY_NULL_IFF_NONE,
        `carry_state_class is "none" but schemas.carry_state is "${String(m.schemas.carry_state)}" (expected null)`);
    }
    if (!isNone && isNull) {
      return fail(BRICK_CARRY_NULL_IFF_NONE,
        `schemas.carry_state is null but carry_state_class is "${m.carry_state_class}" (expected "none")`);
    }
    return pass(BRICK_CARRY_NULL_IFF_NONE);
  },
};

const BRICK_CARRY_NONE_ZERO_BYTES: InvariantRule<BrickManifest> = {
  id: "BRICK_CARRY_NONE_ZERO_BYTES",
  section: "§6.8",
  description: "carry_state_class=none ⇒ carry_state_max_bytes=0",
  target: "brick",
  evaluate: (m) => {
    if (m.carry_state_class === "none" && m.carry_state_max_bytes !== 0) {
      return fail(BRICK_CARRY_NONE_ZERO_BYTES,
        `carry_state_class is "none" but carry_state_max_bytes is ${m.carry_state_max_bytes} (expected 0)`);
    }
    return pass(BRICK_CARRY_NONE_ZERO_BYTES);
  },
};

const BRICK_CARRY_EXISTS_POSITIVE_BYTES: InvariantRule<BrickManifest> = {
  id: "BRICK_CARRY_EXISTS_POSITIVE_BYTES",
  section: "§6.8",
  description: "carry_state_class≠none ⇒ carry_state_max_bytes>0",
  target: "brick",
  evaluate: (m) => {
    if (m.carry_state_class !== "none" && m.carry_state_max_bytes <= 0) {
      return fail(BRICK_CARRY_EXISTS_POSITIVE_BYTES,
        `carry_state_class is "${m.carry_state_class}" but carry_state_max_bytes is ${m.carry_state_max_bytes} (expected >0)`);
    }
    return pass(BRICK_CARRY_EXISTS_POSITIVE_BYTES);
  },
};

const BRICK_WALL_NOT_BIT_EXACT: InvariantRule<BrickManifest> = {
  id: "BRICK_WALL_NOT_BIT_EXACT",
  section: "§6.7",
  description: "time_model=wall_bounded ⇒ determinism.mode≠bit_exact",
  target: "brick",
  evaluate: (m) => {
    if (m.time_model === "wall_bounded" && m.determinism.mode === "bit_exact") {
      return fail(BRICK_WALL_NOT_BIT_EXACT,
        `time_model is "wall_bounded" but determinism.mode is "${m.determinism.mode}" (incompatible)`);
    }
    return pass(BRICK_WALL_NOT_BIT_EXACT);
  },
};

const BRICK_DRIFT_NEEDS_EPSILON: InvariantRule<BrickManifest> = {
  id: "BRICK_DRIFT_NEEDS_EPSILON",
  section: "§6.6",
  description: "bounded_drift ⇒ epsilon present and >0",
  target: "brick",
  evaluate: (m) => {
    if (m.determinism.mode === "bounded_drift") {
      if (m.determinism.epsilon == null || m.determinism.epsilon <= 0) {
        return fail(BRICK_DRIFT_NEEDS_EPSILON,
          `determinism.mode is "bounded_drift" but epsilon is ${m.determinism.epsilon ?? "missing"} (expected >0)`);
      }
    }
    return pass(BRICK_DRIFT_NEEDS_EPSILON);
  },
};

const BRICK_STOCHASTIC_NEEDS_SEED: InvariantRule<BrickManifest> = {
  id: "BRICK_STOCHASTIC_NEEDS_SEED",
  section: "§6.6",
  description: "stochastic ⇒ seed_source=\"ctx.seed\"",
  target: "brick",
  evaluate: (m) => {
    if (m.determinism.mode === "stochastic" && m.determinism.seed_source !== "ctx.seed") {
      return fail(BRICK_STOCHASTIC_NEEDS_SEED,
        `determinism.mode is "stochastic" but seed_source is "${m.determinism.seed_source ?? "missing"}" (expected "ctx.seed")`);
    }
    return pass(BRICK_STOCHASTIC_NEEDS_SEED);
  },
};

const BRICK_MODEL_PIN_NEEDS_STOCHASTIC: InvariantRule<BrickManifest> = {
  id: "BRICK_MODEL_PIN_NEEDS_STOCHASTIC",
  section: "§6.6.1",
  description: "model_pin present ⇒ mode=stochastic",
  target: "brick",
  evaluate: (m) => {
    if (m.determinism.model_pin != null && m.determinism.mode !== "stochastic") {
      return fail(BRICK_MODEL_PIN_NEEDS_STOCHASTIC,
        `model_pin is present but determinism.mode is "${m.determinism.mode}" (expected "stochastic")`);
    }
    return pass(BRICK_MODEL_PIN_NEEDS_STOCHASTIC);
  },
};

const BRICK_MODEL_PIN_NEEDS_REPRO_LEVEL: InvariantRule<BrickManifest> = {
  id: "BRICK_MODEL_PIN_NEEDS_REPRO_LEVEL",
  section: "§6.6.1",
  description: "model_pin present ⇒ reproducibility_level present",
  target: "brick",
  evaluate: (m) => {
    if (m.determinism.model_pin != null && m.determinism.reproducibility_level == null) {
      return fail(BRICK_MODEL_PIN_NEEDS_REPRO_LEVEL,
        "model_pin is present but reproducibility_level is missing");
    }
    return pass(BRICK_MODEL_PIN_NEEDS_REPRO_LEVEL);
  },
};

const BRICK_REPRO_LEVEL_NEEDS_MODEL_PIN: InvariantRule<BrickManifest> = {
  id: "BRICK_REPRO_LEVEL_NEEDS_MODEL_PIN",
  section: "§6.6.1",
  description: "reproducibility_level present ⇒ model_pin present",
  target: "brick",
  evaluate: (m) => {
    if (m.determinism.reproducibility_level != null && m.determinism.model_pin == null) {
      return fail(BRICK_REPRO_LEVEL_NEEDS_MODEL_PIN,
        `reproducibility_level is "${m.determinism.reproducibility_level}" but model_pin is missing`);
    }
    return pass(BRICK_REPRO_LEVEL_NEEDS_MODEL_PIN);
  },
};

const BRICK_SIDE_EFFECTS_NEEDS_CARRY: InvariantRule<BrickManifest> = {
  id: "BRICK_SIDE_EFFECTS_NEEDS_CARRY",
  section: "§6.9",
  description: "side_effects_schema≠null ⇒ carry_state declared",
  target: "brick",
  evaluate: (m) => {
    if (m.carry_state_side_effects_schema != null) {
      if (m.schemas.carry_state === null || m.carry_state_class === "none") {
        return fail(BRICK_SIDE_EFFECTS_NEEDS_CARRY,
          "carry_state_side_effects_schema is set but carry state is not declared " +
          "(schemas.carry_state must be non-null and carry_state_class ≠ \"none\")");
      }
    }
    return pass(BRICK_SIDE_EFFECTS_NEEDS_CARRY);
  },
};

const BRICK_CAPABILITIES_EMPTY: InvariantRule<BrickManifest> = {
  id: "BRICK_CAPABILITIES_EMPTY",
  section: "§6.5",
  description: "capabilities[] must be empty in v0.2",
  target: "brick",
  evaluate: (m) => {
    if (m.capabilities.length > 0) {
      return fail(BRICK_CAPABILITIES_EMPTY,
        `capabilities has ${m.capabilities.length} items (must be empty in v0.2)`);
    }
    return pass(BRICK_CAPABILITIES_EMPTY);
  },
};

const BRICK_FORMAT_WASM: InvariantRule<BrickManifest> = {
  id: "BRICK_FORMAT_WASM",
  section: "§6.2",
  description: "artifact.format must be \"wasm\"",
  target: "brick",
  evaluate: (m) => {
    if (m.artifact.format !== "wasm") {
      return fail(BRICK_FORMAT_WASM,
        `artifact.format is "${m.artifact.format}" (expected "wasm")`);
    }
    return pass(BRICK_FORMAT_WASM);
  },
};

// ── Export ───────────────────────────────────────────────────────────────────

export const brickInvariants: InvariantRule<BrickManifest>[] = [
  BRICK_CARRY_NULL_IFF_NONE,
  BRICK_CARRY_NONE_ZERO_BYTES,
  BRICK_CARRY_EXISTS_POSITIVE_BYTES,
  BRICK_WALL_NOT_BIT_EXACT,
  BRICK_DRIFT_NEEDS_EPSILON,
  BRICK_STOCHASTIC_NEEDS_SEED,
  BRICK_MODEL_PIN_NEEDS_STOCHASTIC,
  BRICK_MODEL_PIN_NEEDS_REPRO_LEVEL,
  BRICK_REPRO_LEVEL_NEEDS_MODEL_PIN,
  BRICK_SIDE_EFFECTS_NEEDS_CARRY,
  BRICK_CAPABILITIES_EMPTY,
  BRICK_FORMAT_WASM,
];
