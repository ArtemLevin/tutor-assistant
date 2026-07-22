---
name: risk-based-testing
description: >
  Select and implement the smallest valuable test set for changed behavior. Use
  for bug fixes, new business logic, state transitions, APIs, persistence,
  concurrency, security-sensitive code, or regression prevention when existing tests are insufficient.
---

# Purpose

Increase confidence without maximizing test count or global line coverage.

# Inputs

- requirements contract;
- changed behavior and symbols;
- existing relevant tests;
- risk classification and verification gaps.

# Workflow

1. Identify the precise behavior or invariant that changed.
2. Determine whether an existing test already protects it.
3. Score risk across blast radius, logic complexity, uncertainty, state/data, security, and concurrency from 0 to 2 each.
4. Select the nearest stable test boundary.
5. Prefer a regression test that fails before the production fix.
6. Add happy, negative, boundary, integration, or concurrency cases only when justified by the score.
7. Run the narrowest selected tests and expand on evidence.
8. Record omitted tests and why they would not materially reduce risk.

# Decision rules

- Do not add tests solely for coverage.
- Do not test standard-library or framework behavior instead of the project's adapter.
- Avoid coupling tests to private implementation details.
- Use integration tests when correctness depends on a real transaction, database constraint, queue, filesystem, or protocol boundary.
- A valuable test protects a contract, catches the original regression, or covers an identified high-risk failure.

# Output

Return risk score, selected tests, omitted tests with reasons, commands, observed results, and remaining unverified risks.

# Stop conditions

Stop when changed behavior is protected and another test would not materially reduce an identified risk.
