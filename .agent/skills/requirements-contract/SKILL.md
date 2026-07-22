---
name: requirements-contract
description: >
  Convert a user request into verifiable engineering constraints. Use before
  standard or deep implementation when acceptance criteria, non-goals,
  compatibility expectations, failure behavior, or scope boundaries must be explicit.
---

# Purpose

Prevent scope drift and make completion objectively testable.

# Inputs

- user request;
- triage result;
- confirmed repository behavior;
- explicit constraints and existing public contracts.

# Workflow

1. State the user-observable goal in one paragraph.
2. Express acceptance criteria as independently verifiable outcomes.
3. Record non-goals and forbidden changes.
4. Identify compatibility requirements for API, storage, configuration, CLI, and operations.
5. Define relevant failure and retry behavior.
6. Separate confirmed requirements from assumptions.
7. Mark unresolved decisions that materially block correctness.

# Decision rules

- Do not invent product behavior to make implementation convenient.
- Preserve existing behavior unless the request explicitly changes it.
- Acceptance criteria describe outcomes, not internal implementation.
- Non-goals should prevent plausible scope expansion, not repeat the entire repository.
- Use visible placeholders or escalate uncertainty when a missing decision changes public behavior.

# Output

Return goal, acceptance criteria, non-goals, compatibility, constraints, failure behavior, assumptions, and unresolved decisions.

# Stop conditions

Stop when each planned production change can be traced to an acceptance criterion or required compatibility constraint.
