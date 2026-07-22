---
name: token-telemetry
description: >
  Use when measuring model/tool usage, enforcing token or call budgets, comparing run efficiency, or diagnosing expensive AgentKit phases.
---

# Purpose

Measure actual usage without inventing unavailable token counts, enforce configured limits, and identify phases that consume disproportionate resources.

# Inputs

- `.agent/agentkit.toml` budget configuration
- one run's `usage.json` and `budget.json`
- optional multi-run `agentkit report` output
- completion status and residual risks

# Workflow

1. Read the latest usage and budget artifacts or invoke the matching Make target.
2. Separate agent calls from local tool calls.
3. Distinguish measured token usage from unknown usage.
4. Compare totals and per-phase calls against soft, hard, and phase-specific limits.
5. Report the largest measured phase and any partial-data caveat.
6. Recommend the smallest configuration or workflow adjustment supported by evidence.

# Decision rules

- Never estimate missing tokens unless an explicit estimator is requested and clearly labelled.
- Cached input tokens are a subset of input usage, not additional tokens.
- A hard-budget violation blocks further model calls.
- A soft-budget warning does not invalidate a correct task by itself.
- Prefer cost per accepted task over minimum tokens per individual call.
- Do not disable verification or review merely to improve usage numbers.

# Output

Return measured totals, unknown calls, limit status, the most expensive phase, and one evidence-backed optimization.

# Stop conditions

Stop when the relevant usage is measured or explicitly marked unavailable, all limit violations are identified, and no unsupported cost claim remains.
