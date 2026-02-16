<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# NCP Design Decisions

This document captures the rationale behind every major architectural choice in the Neural Computation Protocol. It is **not normative** — the canonical spec is [`ncp-v0.2.3.md`](ncp-v0.2.3.md). This document explains the *why*.

---

## Why Pure-Functional Bricks

Neurons are stateless activation functions. Intelligence lives in synaptic weights, not in individual neurons. By the same logic, NCP Bricks are pure functions — all state is explicit and runtime-owned.

Pure functions are:

- **Testable** — given the same inputs, the same output is guaranteed (modulo declared stochasticity).
- **Cacheable** — deterministic results can be memoized by content hash.
- **Replayable** — recorded inputs + pinned versions reproduce execution.
- **Composable** — no hidden side-effects means Bricks can be freely rewired.

Zero ambient authority (no filesystem, no network, no wall clock except declared) eliminates an entire class of security and reproducibility bugs. See Section [6.5](ncp-v0.2.3.md#65-purity-and-capabilities).

---

## Why Three-Layer State Taxonomy

NCP defines three layers of state because they have fundamentally different lifecycles:

1. **Execution state** (ephemeral) — values inside a single invoke. Born and destroyed within one Brick call. No persistence, no sharing.
2. **Graph state** (runtime-owned) — two sub-layers:
   - **Synaptic state** (cold, versioned) — weights, thresholds, topology params. Changes only when a new graph version is published. Analogous to trained synaptic weights in a neural network.
   - **Carry state** (hot, session-scoped) — activation vectors, sliding windows, counters. Changes on every invoke. Analogous to short-term memory or recurrent hidden state.
3. **Observability state** (append-only) — trace records, audit logs, obs_events. Write-only from the Brick's perspective. Never fed back into computation.

This taxonomy prevents the common anti-pattern where "state" is an undifferentiated blob that mixes config, session memory, and audit logs. See Section [3.3](ncp-v0.2.3.md#33-state-taxonomy).

---

## Why Symbolic Slot Binding

Bricks declare abstract **slots** (e.g., `SENTIMENT_ROUTE_PRIOR`); Graphs bind slots to concrete ref sources. This is a capability model:

- Bricks never see the graph topology or raw storage.
- The runtime enforces that a Brick can only read refs for its declared slots.
- Slot bindings are auditable and version-pinned.

This design means the same Brick can be reused across different graphs with different weight sources, without modification. See Section [6.10](ncp-v0.2.3.md#610-graph-ref-slots-capabilities).

---

## Why Typed Errors with Structural Boundaries

NCP defines five error classes (`INVALID_INPUT`, `COMPUTATION_ERROR`, `RESOURCE_EXCEEDED`, `RUNTIME_REJECTED`, `LOW_CONFIDENCE`) because error recovery is a graph topology concern — different error classes route to different recovery nodes. See Section [9.4](ncp-v0.2.3.md#94-error-classes-v02).

The v0.2.3 three-variant Result union enforces structural boundaries by schema shape:

- **Success** — output present, no error, carry state updated.
- **LowConfidence** — output present, error present (LOW_CONFIDENCE only), carry state updated.
- **Failure** — no output, error present (any class except LOW_CONFIDENCE), carry state NOT updated.

This eliminates ambiguous intermediate states. A consumer never has to guess whether an output is trustworthy — the variant tells them.

---

## Why Three-Variant Result Union (v0.2.3)

The v0.2.2 two-variant model (Success/Failure) created a contradiction: Section 9.2 required that if output is present, error_class MUST be `LOW_CONFIDENCE` — but the Failure variant had no output field. A `LOW_CONFIDENCE` result needs both output and error, which neither variant could express.

The fix (applied in the canonical spec [`ncp-v0.2.3.md`](ncp-v0.2.3.md#91-result-union)): a third variant (`LowConfidence`) that carries both output and error. Each shape is self-describing. `carry_state_next` applies on Success and LowConfidence (Brick completed). `carry_state_side_effects` applies only on Failure. The structural boundary is enforced by schema, not runtime validation. See Section [9.2](ncp-v0.2.3.md#92-structural-boundary-rule-mandatory).

---

## Why CBOR over Protobuf

- **Self-describing** — CBOR payloads carry their own type information; no external `.proto` file needed.
- **Canonical form** — RFC 8742 defines deterministic encoding, which NCP requires for digest/signature computation.
- **IETF standard** — RFC 8949, widely supported across languages.
- **Binary efficiency** — compact representation without the complexity of Protobuf's schema evolution model.

JSON is supported for tooling/debugging but is explicitly excluded from digest/signature computation. See Section [16.1](ncp-v0.2.3.md#161-normative-encoding).

---

## Why WASM-Only for v0.2

One artifact format means:

- **One security model** — a single sandbox specification to audit and test.
- **One ABI** — a single entrypoint signature (`invoke`) and memory management contract.
- **One conformance suite** — determinism, sandbox, and wire compatibility tests target a single runtime.

Future formats (e.g., `wasm_aot`, `intrinsic`, `native_sandboxed`) are reserved via the Section [6.2](ncp-v0.2.3.md#62-artifact) extension point, but only when a ratified protocol version defines conformance criteria for them.

---

## The Biological Metaphor

NCP's architecture is inspired by computational neuroscience:

- **Bricks ≈ neurons** — stateless activation functions with typed inputs and outputs.
- **Graphs ≈ neural circuits** — topology determines behavior; intelligence is emergent.
- **Synaptic state ≈ trained weights** — cold, versioned, changed only through learning (new graph versions).
- **Carry state ≈ short-term memory** — hot, session-scoped, updated on every activation.
- **Graph refs ≈ synaptic connections** — symbolic bindings that the runtime resolves.

> **Note:** Biological neurons have internal dynamics; "stateless" here refers to the computational abstraction used for enforceable purity. The metaphor guides architecture, not biological fidelity.

This is not mere analogy — it drives concrete design decisions. Neurons don't own their weights; Bricks don't own their state. Learning produces new synaptic configurations; training produces new graph versions. The runtime is the "brain" that routes signals and owns all state.

---

## Cost Model (Illustrative)

> These figures are illustrative placeholders until we publish measured benchmarks. They are intended to convey the order-of-magnitude advantage, not to make precise claims.

**Assumptions (example):**

- 10,000 requests/hour
- 10% LLM escalation rate (only ambiguous cases reach the LLM)
- LLM call cost: ~$0.003/call (mid-tier model, ~1K tokens in + out)
- NCP Brick call cost: ~$0.000003/call (WASM execution, no external API)

| Architecture                              | Estimated cost/hr (illustrative) |
|-------------------------------------------|----------------------------------:|
| Monolithic LLM (all reasoning via LLM)    |                           ~$300   |
| Multi-agent (multiple LLM agents)         |                         ~$1,000   |
| NCP hybrid (90% Bricks + 10% LLM)        |                            ~$30   |

**10x–33x cost reduction** comes from routing the vast majority of computation through cheap, deterministic Bricks and reserving expensive LLM calls for genuinely ambiguous cases. The `estimated_cost_per_invoke_usd` field (Section [6.4](ncp-v0.2.3.md#64-resource-limits)) enables tooling to model and alert on cost at design time.

---

## PIC Standard Integration

NCP's trigger provenance (Section [8.1](ncp-v0.2.3.md#81-context-ctx)) provides a causal chain for every invocation:

- `trigger.source_node_id`, `trigger.source_step`, `trigger.edge_id`, `trigger.routing_reason`
- Root invocations use the `__root__` sentinel.

Walking the trigger chain produces a complete provenance certificate — every decision can be traced back to its causal inputs. This aligns with provenance, integrity, and confidentiality (PIC) requirements for auditable AI systems.

---

## MCP Integration

NCP and MCP (Model Context Protocol) operate at different layers:

- **NCP** is infrastructure — it defines how computation units are sandboxed, composed, and audited.
- **MCP** is interface — it defines how AI models discover and call tools.

Integration patterns (see [Appendix C](ncp-v0.2.3.md#appendix-c-integration-patterns-non-normative)):

- **C.1**: An NCP Graph is exposed as a single MCP tool. The MCP client sees one tool call; internally, NCP executes the full Brick network.
- **C.2** (future): A Brick delegates to an MCP server as a backend. Requires extending `capabilities[]` beyond empty — explicitly deferred to a future protocol version.

---

## Technology Alignment

NCP's design bets on three converging trends:

1. **LLM cost curves** — inference costs are falling, but still too high for commodity routing. NCP makes LLM calls surgical rather than pervasive.
2. **WASM maturation** — WASI and component model adoption is accelerating, making WASM a credible universal sandbox.
3. **Neuromorphic hardware** — NCP's pure-functional, graph-routed model maps naturally to hardware that implements neuron-like activation and routing at the silicon level.
