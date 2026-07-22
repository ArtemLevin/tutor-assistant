---
name: evaluation-harness
description: >
  Use when measuring AgentKit engineering outcomes across deterministic fixture tasks, repeated runs, configuration variants, and historical comparisons.
---

# Purpose

Evaluate correctness, efficiency, and quality as separate evidence dimensions so optimization claims cannot hide failed acceptance checks or readiness regressions.

# Inputs

- committed evaluation manifest;
- deterministic fixture repository;
- AgentKit configuration and experiment dimensions;
- acceptance commands and file constraints;
- optional quality and budget expectations;
- one or more completed evaluation summaries for comparison.

# Workflow

1. Validate the manifest and reject secret-like experiment fields.
2. Hash the source fixture and copy it into an isolated workspace.
3. Initialize a deterministic Git baseline inside the copy.
4. Run AgentKit and execute explicit acceptance commands.
5. Collect completion, review, scope, usage, context, and quality evidence.
6. Verify that the source fixture remained unchanged.
7. Aggregate repeated runs without converting unknown usage into zero.
8. Compare compatible summaries with explicit regression thresholds.
9. Persist JSON and bounded Markdown reports.

# Decision rules

- Correctness regressions dominate efficiency improvements.
- Token averages include measured runs only and report unknown calls separately.
- Quality metrics remain unavailable when evidence is absent.
- Threshold equality passes; only strict exceedance is a regression.
- Never publish secret values, environment dumps, or full unbounded command output.
- Full provider-cost suites are opt-in; smoke suites use manifests marked `smoke: true`.

# Output

Return immutable manifest evidence, per-run results, dimension-separated summary metrics, explicit warnings, and comparison regressions or improvements.

# Stop conditions

Stop when fixture preservation is proven, acceptance evidence is durable, unknown measurements are explicit, and no conclusion relies on a single opaque composite score.
