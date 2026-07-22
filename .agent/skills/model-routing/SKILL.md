---
name: model-routing
description: >
  Use when configuring, explaining, testing, or diagnosing phase-aware model routes and bounded OpenAI fallbacks in AgentKit.
---

# Purpose

Choose an executor per workflow phase while preserving AgentKit's mutation boundary, usage accounting, and deterministic fallback limits.

# Inputs

- `.agent/agentkit.toml` model targets, routes, and fallbacks
- task mode and optional `--route` override
- `model-route.json`, `model-attempts.json`, and `usage.json`
- provider availability and API-key environment status

# Workflow

1. Run `agentkit models doctor` without making a paid request.
2. Inspect the configured targets with `agentkit models list`.
3. Explain the intended phase selection with `agentkit models route --task "..." --explain`.
4. Confirm that `implementation` and `targeted_fix` use a local mutation-capable CLI target.
5. Confirm that direct OpenAI targets are limited to read-only `plan` or `review` phases.
6. Verify retry and fallback counts against the persisted attempt artifact.
7. Compare exact measured usage and accepted-task quality before changing a default route.

# Decision rules

- Never put an OpenAI Responses target in a phase that mutates the local workspace.
- Keep API keys in named environment variables; never write their values to configuration or artifacts.
- Treat a schema-invalid review as a failed provider call.
- Retry only transient provider failures and never exceed the configured bound.
- Do not switch providers after a mutating phase fails because the local diff may be partial.
- A live provider test is opt-in because it may incur cost.
- Prefer evaluation evidence over provider marketing claims when changing route defaults.

# Output

Return the selected route, target for each phase, capability constraint, configured fallback bound, measured usage availability, and any configuration error.

# Stop conditions

Stop when every phase has one valid primary target, mutating phases remain local, fallbacks are bounded, and the route can be reproduced from committed configuration.
