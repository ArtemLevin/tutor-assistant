---
name: autopilot-orchestration
description: >
  Use when AgentKit is running a task end-to-end and must coordinate triage, Graphify context, implementation, verification, review, bounded fixes, and completion gates.
---

# Purpose

Coordinate the engineering workflow without replacing the responsibilities of individual specialist skills.

# Inputs

- task packet
- AgentKit project configuration
- selected skills
- Graphify scoped context
- Git baseline

# Workflow

1. Validate the Git and configuration preflight.
2. Run triage and activate only required skills.
3. Build a bounded Graphify context.
4. Invoke implementation with explicit scope and safety constraints.
5. Run configured or conservative auto-discovered verification.
6. Invoke read-only adversarial review.
7. Apply only blocking fixes within the iteration budget.
8. Evaluate the completion gate and persist run artifacts.

# Decision rules

- Deep mode requires explicit approval before implementation.
- Do not commit, push, merge, deploy, or perform irreversible operations.
- Do not treat an empty check set as successful verification.
- Review must not mutate the working tree.
- Unstructured review output fails closed.

# Output

Persist triage, graph context, task packet, command results, verification, review, and completion reports under `.agent/state/runs/`.

# Stop conditions

Stop as ready for human review only when checks pass, no P0/P1 finding remains, and scope limits hold; otherwise stop with a precise failure state.
