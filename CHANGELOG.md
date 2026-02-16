<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# Changelog

All notable changes to the NCP specification are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Brick versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). NCP protocol versions follow the versioning policy in the spec (v0.2.x patch-compatible, breaking changes require v0.3.0).

## [Unreleased]

### Changed
- Result model: three-variant discriminated union (Success, LowConfidence, Failure) replacing two-variant model
- Section 9.2 (Structural Boundary Rule) updated for three-variant semantics
- Section 9.5 (Confidence Signaling) updated for LowConfidence variant
- Carry state semantics: `carry_state_next` applies on Success and LowConfidence; `carry_state_side_effects` applies only on Failure

### Fixed
- Resolved contradiction between Section 9.1 (two-variant Result) and Section 9.2 (Structural Boundary Rule requiring output on LOW_CONFIDENCE)
- Fixed duplicate "Appendix B" header in spec document

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
