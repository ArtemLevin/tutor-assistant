---
name: change-planner
description: >
  Produce a compact implementation plan tied to repository evidence. Use for
  standard and deep tasks, multi-file changes, behavior changes, or refactors
  where order, ownership, verification, compatibility, or rollback matters.
---

# Purpose

Create an executable change contract rather than a generic to-do list.

# Inputs

- requirements contract;
- context ledger;
- execution mode and budgets;
- triggered specialist constraints.

# Workflow

1. Choose the smallest layer that owns each required invariant.
2. Break the change into independently reviewable steps.
3. For every step identify file or symbol, action, rationale, verification, and dependencies.
4. State files and contracts that must not change.
5. Include rollback or forward-recovery considerations for high-risk changes.
6. Order steps so tests or diagnostics expose the original defect where practical.
7. Check the plan against acceptance criteria and non-goals.

# Decision rules

- Skip a formal plan for trivial fast-mode edits.
- Do not use steps such as “study code”, “fix problem”, or “add tests” without exact targets.
- Do not add opportunistic cleanup.
- Prefer one owner for each invariant.
- Re-plan only when evidence invalidates a material assumption.

# Output

Return data compatible with `schemas/plan.schema.json`.

# Stop conditions

Stop when every step has a target, reason, expected behavioral delta, and verification method, and no step exists only for style.
