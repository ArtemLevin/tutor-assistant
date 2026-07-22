---
name: verification-router
description: >
  Select the cheapest sufficient verification commands for a change. Use after
  implementation to map changed files and behavior to syntax checks, linters,
  type checks, unit, integration, contract, migration, security, or smoke tests.
---

# Purpose

Avoid both under-verification and wasteful execution of unrelated test suites.

# Inputs

- changed files and symbols;
- behavioral delta;
- risk classification;
- project manifests, CI configuration, and nearest tests.

# Workflow

1. Identify commands already used by the repository and CI.
2. Map each acceptance criterion and risk to an observable check.
3. Order checks from fastest and narrowest to broader and more expensive.
4. Define expansion conditions for each failed or ambiguous result.
5. Trigger `risk-based-testing` when existing checks do not protect changed behavior.
6. Trigger specialist review when the diff crosses its risk boundary.
7. Record commands exactly before execution.

# Decision rules

- Prefer repository-native commands over invented wrappers.
- Run syntax/import checks before expensive suites.
- Run nearest tests before package, service, or full-repository suites.
- Do not use a full suite as a substitute for a missing targeted regression test.
- Do not claim an unavailable environment validates integration behavior.

# Output

Return ordered checks, rationale, expected signal, expansion conditions, and triggered skills.

# Stop conditions

Stop when every acceptance criterion and identified high-risk failure has a check or an explicitly disclosed verification gap.
