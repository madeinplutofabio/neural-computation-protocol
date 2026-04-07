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

The fix (applied in the canonical spec [`ncp-v0.2.3.md`](ncp-v0.2.3.md#91-result-union)): a third variant (`LowConfidence`) that carries both output and error. Each shape is self-describing. `carry_state_next` applies on Success and LowConfidence (Brick completed). `carry_state_side_effects` applies only on Failure. The structural boundary is enforced by the variant shape and MUST be validated by the runtime. See Section [9.2](ncp-v0.2.3.md#92-structural-boundary-rule-mandatory).

---

## Why CBOR over Protobuf

- **Self-describing** — CBOR payloads carry their own type information; no external `.proto` file needed.
- **Canonical form** — Deterministic encoding per RFC 8949 §4.2 is required for digest/signature computation.
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

---

## Phase 2 Reference Runtime Decisions

These decisions govern the Phase 2 reference runtime implementation. They are implementation choices, not protocol-level requirements — other runtimes may make different choices while remaining spec-conformant.

### WASM Engine: Wasmtime

The reference runtime uses [Wasmtime](https://wasmtime.dev/) (Bytecode Alliance) as its WASM execution engine.

Rationale:

- Most mature Rust-native WASM runtime with a stable embedding API.
- First-class support for **fuel metering** (enables deterministic compute budgeting; can be calibrated to `limits.max_ms` in the reference runtime), **memory limits** (maps to `limits.max_mem_mb`), and **trap catching** (maps to Section [16.2.1](ncp-v0.2.3.md#1621-wasm-trap-handling)).
- Actively maintained by the Bytecode Alliance (Fastly, Mozilla, et al.).
- **Module compilation** is performed once per Brick; **instantiation** is performed per invocation. This amortizes the compilation cost.

### Input Memory Policy: Runtime Uses Brick's Exported alloc/free

The runtime never allocates inside WASM memory directly. It only writes into regions returned by the Brick's exported `alloc`, and always frees via the Brick's exported `free` (Model B, Section [16.3](ncp-v0.2.3.md#163-memory-management)).

Invocation flow:

1. `envelope_ptr = brick.alloc(envelope_len)` — allocate space for the CBOR envelope.
2. Runtime writes the envelope bytes into `[envelope_ptr, envelope_ptr + envelope_len)`.
3. `result_ptr = brick.invoke(envelope_ptr, envelope_len)` — execute the Brick.
4. Runtime reads the result from `result_ptr` (4-byte LE length prefix + CBOR payload).
5. `brick.free(envelope_ptr, envelope_len)` — free the envelope buffer.
6. `brick.free(result_ptr, 4 + result_len)` — free the result buffer.

**OOM handling:** If `alloc(envelope_len)` returns `0`, the runtime MUST treat the invocation as `Failure` with `error_class: RESOURCE_EXCEEDED` without calling `invoke`. This mirrors the Brick-side OOM behavior (trap → `COMPUTATION_ERROR`) but at the runtime level.

### Bundle Verification: Digest + Size on Load, Result Boundaries at Runtime

**On load:**

- Verify `sha256(wasm_bytes) == artifact.digest`. Reject on mismatch.
- Verify `wasm_bytes.len() == artifact.size_bytes`. Reject on mismatch.
- Validate the manifest structure and cross-field invariants (reuse `ncp-validate` logic).

**At runtime (per invocation):**

- Validate result structural boundaries per Section [9.2](ncp-v0.2.3.md#92-structural-boundary-rule-mandatory):
  - Result MUST decode as valid CBOR.
  - Result MUST match one of `Success` / `LowConfidence` / `Failure` shapes.
  - Enforce output present/absent rules and `LOW_CONFIDENCE` restrictions.
- Full JSON Schema validation of output payloads against `schemas.output` is deferred to Phase 3.

### Trace Format: JSON Lines to stderr

Traces are emitted as **JSON Lines** (one JSON object per line, newline-delimited).

- Default output: stderr (keeps stdout clean for final graph output; composable with `2> trace.jsonl`).
- `--trace <file>` flag: writes traces to a file instead of stderr.
- Fields: exactly Section [11.1](ncp-v0.2.3.md#111-minimal-trace-record-mandatory) minimal trace record.
- **Header line:** The first trace line is a `runtime_info` record containing `runtime_version` and `wasmtime_version`, emitted once at startup for trace interpretability.

### Instance Lifecycle: Ephemeral (One Invoke per Instance)

The reference runtime creates a **fresh WASM instance per invocation**. No instance reuse across invocations.

Rationale:

- Simplest correct implementation — no state leakage, no allocator concerns.
- Wasmtime instantiation is fast (~microseconds for small modules).
- Matches the echo brick's documented assumption ("ephemeral instances only").
- Compiled `wasmtime::Module` objects are reused; only `Instance` is created fresh.

Production runtimes MAY implement instance pooling or reuse as an optimization, provided they guarantee equivalent isolation semantics.
