---
name: implementation
description: >
  Apply an approved software change as a minimal, coherent diff. Use after context
  and requirements are sufficient to modify code, configuration, tests, or documentation
  while preserving unrelated behavior and project conventions.
---

# Purpose

Implement the smallest complete change supported by repository evidence.

# Inputs

- approved requirements contract and plan, or fast-mode task statement;
- context ledger;
- allowed files and compatibility constraints;
- project style and commands.

# Workflow

1. Confirm the target files have not changed since planning when possible.
2. Modify the current owner of the affected behavior.
3. Reuse existing utilities, error types, fixtures, and conventions.
4. Keep the diff limited to required production code, tests, and documentation.
5. Add comments only for rationale, invariants, ordering, compatibility, or non-obvious constraints.
6. Run the cheapest syntax or formatting check after editing.
7. Produce a changed-symbol and behavioral-delta summary for verification and review.

# Decision rules

- Do not rename or reformat unrelated code.
- Do not add a dependency when existing primitives are sufficient.
- Do not catch broad exceptions when a specific boundary is known.
- Do not add silent fallback behavior that masks failure.
- Do not change public signatures or stored representations implicitly.
- Do not exceed the approved iteration budget without new evidence.

# Output

Return changed files and symbols, behavioral delta, assumptions validated or invalidated, and immediate checks executed.

# Stop conditions

Stop when the approved change is implemented, the diff is coherent, and further edits require verification or review evidence.
