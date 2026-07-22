---
name: quality-regression-gate
description: >
  Use when comparing quality snapshots, evaluating absolute or delta thresholds, and deciding whether a maintainability regression should report, warn, or block completion.
---

# Purpose

Compare schema-valid quality evidence without treating missing measurements as improvement, then apply explicit report, warn, or enforce policy.

# Inputs

- `quality-before.json`
- `quality-after.json`
- `quality-diff.json`
- `[quality]`, `[quality.absolute]`, and `[quality.delta]`
- source code and executed tests for interpretation

# Workflow

1. Verify provider, version, language, and configuration comparability.
2. Compute directional project metric deltas.
3. Classify new, resolved, persisting, and changed hotspots.
4. Evaluate only configured absolute and delta thresholds.
5. Apply unavailable-data policy explicitly.
6. Write `quality-gate.json` and update completion evidence.
7. Keep report and warn modes non-blocking.

# Decision rules

- Never treat a missing value as zero or as an improvement.
- Higher score, RP, OP, and density are treated as worse by the current gate.
- Threshold equality passes; only a strict exceedance violates.
- Source code and tests outrank static quality metrics.
- Merge-base analysis must use a temporary worktree and never replace the user worktree.
- Enforce mode blocks only configured violations or unavailable_policy=stop.

# Output

Return metric, baseline, current, delta, threshold, scope, comparability warnings, gate mode, and whether completion is allowed.

# Stop conditions

Stop when the final post-fix snapshot is compared once, every missing measurement is explicit, and the gate artifact is reproducible.
