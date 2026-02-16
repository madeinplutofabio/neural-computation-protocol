<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# Contributing to NCP

Thank you for your interest in contributing to the Neural Computation Protocol.

## How to Contribute

### Reporting Issues

Use the appropriate issue template:

- **[Bug in Spec](../../issues/new?template=bug-in-spec.md)** — contradictions, ambiguities, or errors in the specification
- **[Clarification Request](../../issues/new?template=clarification.md)** — unclear spec language or intent
- **[Implementation Question](../../issues/new?template=implementation-question.md)** — questions about building runtimes, Bricks, or tooling
- **[Proposed Extension](../../issues/new?template=proposed-extension.md)** — feature proposals for future NCP versions

### Submitting Changes

1. Fork the repository
2. Create a feature branch from `main`
3. Make your changes
4. Ensure all checks pass:
   ```bash
   cd tools/ncp-validate
   npm install && npm run build
   npm test
   npm run validate-examples
   ```
5. Submit a pull request using the PR template

### Types of Changes

#### Spec Clarifications (non-breaking, v0.2.x)

Clarifications fix ambiguous language without changing protocol semantics. These can be submitted as direct PRs and are versioned as patch releases.

#### Normative Changes (breaking, requires version bump)

Changes to normative sections (MUST/SHOULD/MAY requirements) follow an RFC-style process:

1. Open a **Proposed Extension** issue describing the change and motivation
2. Discussion period (minimum 14 days)
3. If accepted, submit a PR with spec changes, schema updates, validator updates, and test fixtures
4. Version bump determined by backward compatibility impact

#### Schema, Validator, and Documentation Updates

- Schema changes must align with the canonical spec
- Validator updates must include test fixtures (positive and negative)
- All examples must validate green after changes

## Development Setup

```bash
git clone https://github.com/<org>/ncp.git
cd ncp/tools/ncp-validate
npm install
npm run build
npm test
```

## Style Guidelines

- **Spec language:** Use RFC 2119 keywords (MUST, SHOULD, MAY) for normative requirements
- **Code:** TypeScript strict mode, consistent with existing patterns
- **Commits:** Clear, descriptive messages referencing spec sections where applicable
- **YAML examples:** 2-space indentation, comments for non-obvious fields

## Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). By participating, you are expected to uphold this code.

## License

By contributing, you agree that your contributions will be licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.
