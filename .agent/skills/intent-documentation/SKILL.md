---
name: intent-documentation
description: >
  Add or review comments, docstrings, ADRs, and operational notes that explain
  rationale, invariants, constraints, compatibility, or trade-offs. Use when code
  contains a non-obvious decision; do not comment obvious syntax or narrate implementation steps.
---

# Purpose

Preserve the reasoning future maintainers cannot reliably infer from the code alone.

# Inputs

- changed code and contract;
- architectural or operational constraints;
- rejected simpler alternatives;
- existing documentation style.

# Workflow

1. Identify surprising behavior, ordering constraints, workarounds, and compatibility promises.
2. Decide the correct surface: name, code structure, comment, docstring, README, runbook, or ADR.
3. Prefer clearer code over explanatory comments when both express the same fact.
4. Write comments that state why an apparently simpler alternative is unsafe.
5. Write docstrings as caller-facing contracts: inputs, outputs, errors, side effects, retry/idempotency semantics.
6. Create an ADR for long-lived technology or boundary decisions with meaningful alternatives.
7. Remove comments that merely repeat operations or have become false.

# Decision rules

- Comments explain why, not what.
- Do not encode temporary task history in production comments.
- Do not document a promise the implementation or tests do not enforce.
- Keep rationale close to the decision it constrains.
- Use `templates/adr.md` for durable architectural trade-offs.

# Output

Return documentation added, rationale preserved, redundant comments removed, and any decision intentionally left self-documenting.

# Stop conditions

Stop when non-obvious constraints are discoverable and no comment merely narrates syntax.
