---
name: quality-diagnostics
description: >
  Use when collecting or interpreting bounded code-health evidence from a configured quality provider without treating metrics as proof of a defect.
---

# Purpose

Collect deterministic, machine-readable maintainability evidence, identify bounded hotspots, and explain availability or uncertainty without changing completion semantics.

# Inputs

- `.agent/agentkit.toml` quality configuration
- `quality-provider.json`
- `quality-before.json`
- `quality-hotspots.json`
- relevant source code for any hotspot being interpreted

# Workflow

1. Check provider availability and supported language.
2. Reuse a valid content-addressed snapshot when available.
3. Run project-level analysis first unless details are explicitly required.
4. Escalate to bounded detail only when configured or when project status is elevated.
5. Read source code before converting a metric hotspot into a concrete recommendation.
6. Report provider limits, truncation, and missing fields explicitly.

# Decision rules

- Quality metrics are navigation and risk evidence, not proof that behavior is wrong.
- Never replace a missing metric with zero.
- Never place the complete raw provider report into an agent prompt.
- Prefer source code and executed tests over static quality inference.
- In report mode, findings do not block completion.
- Keep hotspot lists bounded by configuration.

# Output

Return availability, compact project metrics, bounded hotspots, artifact paths, warnings, and the smallest evidence-backed next action.

# Stop conditions

Stop when the snapshot is current, bounded, schema-valid, and every unavailable or partial field is explicit.
