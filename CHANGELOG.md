<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# Changelog

All notable changes to the NCP specification and its reference tooling
(schemas, examples, validator) are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Brick versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). NCP protocol versions follow the versioning policy in the spec (v0.2.x patch-compatible, breaking changes require v0.3.0).

## [Unreleased]

## [0.2.3] — 2026-02-17

### Changed
- Result model clarified as a three-variant discriminated union (Success, LowConfidence, Failure)
- Section 9.2 (Structural Boundary Rule) updated for three-variant semantics
- Section 9.5 (Confidence Signaling) updated for LowConfidence variant
- Carry state semantics: `carry_state_next` applies on Success and LowConfidence; `carry_state_side_effects` applies only on Failure

### Fixed
- Resolved contradiction between Section 9.1 (two-variant Result text) and Section 9.2 (Structural Boundary Rule requiring output on LOW_CONFIDENCE)
- Fixed duplicate "Appendix B" header in spec document

### Added
- JSON Schemas (Draft 2020-12) for Brick Manifest, Graph Manifest, Invocation Envelope, and Result Model
- `ncp-validate` CLI: structural (JSON Schema) + cross-field invariant validation for Brick and Graph manifests
- 24 invariant rules (12 brick, 8 graph, 4 cross) with spec section references
- Example manifests from spec appendices (sentiment-gate, llm-escalation, support-routing)
- Test suite with positive and negative fixtures for all invariants
- CI workflow (GitHub Actions)

## [0.2.2] — TBD

### Added
- Artifact extension point (Section 6.2)
- Stochastic model support with `model_pin` and `reproducibility_level` (Section 6.6.1)
- MCP integration appendix (Appendix C)
- Cost metadata (`estimated_cost_per_invoke_usd`) in resource limits
- Carry state side-effects channel (Section 6.9)

## [0.2.0] — TBD

### Added
- Fan-in activation policies (Section 7.0)
- Trigger provenance in invocation envelope (Section 8.1)
- Carry state lifecycle management (Section 10.5)
- Wire format and transport specification (Section 16)
- Conformance suites (Section 15)

## [0.1.0] — TBD

### Added
- Initial draft: Brick and Graph manifest structure
- Core concepts: Brick, Graph, Runtime, State Taxonomy
- Identifier format and Brick bundle specification
- Basic invocation envelope and result model
