---
name: engineering-balance
description: >
  Balance simplicity, reliability, extensibility, performance, compatibility,
  test cost, and token cost. Use when choosing between a local fix and a broader
  design, introducing abstractions or dependencies, or evaluating possible overengineering.
---

# Purpose

Select the least complex solution that covers confirmed requirements and risks.

# Inputs

- requirements contract;
- candidate solutions;
- risk and blast radius;
- existing extension points and measured constraints.

# Workflow

1. Compare candidates across correctness, reversibility, compatibility, operational risk, implementation cost, test cost, and future lock-in.
2. Identify which future requirements are confirmed and which are hypothetical.
3. Prefer the minimal useful change that preserves the nearest expected extension.
4. Require evidence for performance optimizations and broad abstractions.
5. State the trade-off being accepted rather than claiming a universally best design.

# Decision rules

Create a new abstraction only when at least one is true:

- two or more real consumers share a stable contract;
- an external contract requires replaceable implementations;
- the abstraction isolates a dangerous infrastructure dependency.

Do not optimize an unmeasured path. Do not trade correctness for token savings. Do not preserve compatibility that the user explicitly asked to remove.

# Output

Return selected option, decisive evidence, accepted trade-offs, rejected alternatives, and conditions that would justify revisiting the choice.

# Stop conditions

Stop when one option satisfies all confirmed requirements with the lowest justified complexity and residual risk is explicit.
