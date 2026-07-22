---
name: task-triage
description: >
  Classify a software-engineering request before repository work. Use for every
  non-trivial code, configuration, infrastructure, review, or debugging task to
  select execution mode, risk level, relevant skills, scope, and context budget.
---

# Purpose

Route the task through the smallest workflow that can provide sufficient confidence.

# Inputs

- user request;
- repository metadata and shallow tree;
- known project conventions;
- current working-tree status when available.

# Workflow

1. Classify the task type: documentation, bugfix, feature, refactor, review, migration, operations, or investigation.
2. Identify user-visible behavior and potentially affected contracts.
3. Estimate blast radius and irreversible consequences.
4. Select `fast`, `standard`, or `deep` mode.
5. List required and optional skills using explicit triggers.
6. Set initial file-reading and iteration budgets.
7. Record assumptions that must be validated by repository context.

# Decision rules

- Use `fast` only for local, reversible changes with obvious verification.
- Use `deep` for auth, secrets, destructive data changes, migrations, concurrency, distributed side effects, production infrastructure, or public compatibility.
- Do not activate every specialist review defensively.
- Escalate mode when new evidence increases risk; do not downgrade without evidence.
- Do not solve the task during triage.

# Output

Return data compatible with `schemas/triage.schema.json`.

# Stop conditions

Stop after mode, risk, scope hypothesis, required skills, budgets, and validation assumptions are explicit.
