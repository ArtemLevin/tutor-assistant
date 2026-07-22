---
name: architecture-guard
description: >
  Check whether a proposed change respects existing architectural boundaries.
  Use for new dependencies, cross-layer behavior, public contracts, service boundaries,
  shared state, infrastructure abstractions, or broad refactors; do not redesign the system by default.
---

# Purpose

Prevent local fixes from creating a second source of truth or violating established ownership.

# Inputs

- requirements contract;
- repository context and dependency direction;
- proposed plan or diff;
- relevant architecture documentation.

# Workflow

1. Identify the current owner of each changed invariant.
2. Check dependency direction and layer responsibilities.
3. Look for duplicated state, bypassed abstractions, cycles, and infrastructure leakage.
4. Check whether a public contract changes implicitly.
5. Evaluate new dependencies and abstractions against demonstrated use cases.
6. Compare the proposed solution with the smallest architecture-compatible alternative.
7. Return approval, constraints, or a blocking objection with evidence.

# Decision rules

- Preserve existing boundaries unless they are the documented source of the defect.
- Do not recommend a rewrite when a local compliant fix exists.
- Reject process-local coordination for multi-process invariants.
- Reject a second source of truth unless synchronization semantics are explicit.
- Distinguish architecture defects from stylistic preferences.

# Output

Return decision, constraints, violated boundaries, rejected options with reasons, and the smallest compatible alternative.

# Stop conditions

Stop when the plan has an explicit invariant owner and no unaddressed boundary violation remains.
