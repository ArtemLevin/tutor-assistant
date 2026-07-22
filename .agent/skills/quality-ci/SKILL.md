---
name: quality-ci
description: >
  Use when installing, previewing, or interpreting the read-only AgentKit quality workflow for pull requests and local merge-base checks.
---

# Purpose

Run the same provider-neutral quality lifecycle locally and in GitHub Actions, publish bounded evidence, and preserve gate exit semantics.

# Inputs

- full Git history and a resolvable base ref;
- `.agent/agentkit.toml` quality and quality.ci configuration;
- configured quality provider;
- native quality baseline, current, diff, and gate artifacts.

# Workflow

1. Confirm the clone is not shallow.
2. Resolve the configured base ref and merge-base.
3. Analyze the baseline in a detached temporary worktree.
4. Analyze the current worktree through the provider abstraction.
5. Compare snapshots and apply the existing quality gate.
6. Write bounded Markdown summary and downloadable artifacts.
7. Return the original quality exit code after summary and upload steps.

# Decision rules

- Never use the pull-request head as its own clean baseline.
- Never hard-code a provider command in the workflow.
- Keep default GitHub permissions read-only.
- Do not overwrite a user-modified workflow without explicit `--force`.
- Always upload evidence after a gate failure.
- Missing or non-comparable measurements remain explicit.

# Output

Return the run id, resolved merge-base, gate result, exit code, summary path, artifact directory, and warnings.

# Stop conditions

Stop after artifacts and summary are durable and the configured gate exit code is preserved.
