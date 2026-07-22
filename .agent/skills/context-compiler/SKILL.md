---
name: context-compiler
description: >
  Use when compiling phase-specific minimal context, building or inspecting a project profile, reusing cached repository context, or pruning stale context entries.
---

# Purpose

Reduce repeated repository reads by compiling a deterministic, bounded context packet from the task, project profile, selected skills, candidate files, and symbol signatures.

# Inputs

- task text and execution phase
- `.agent/agentkit.toml` context settings
- `.agent/project-profile.json`
- selected skill names
- repository files and control files
- `.agent/cache/context.db`

# Workflow

1. Build or validate the project profile fingerprint.
2. Select only task-relevant candidate paths.
3. Extract compact symbol signatures where supported.
4. Load summaries only for selected skills.
5. Compute a content-addressed fingerprint from task, phase, profile, selected files, and skill files.
6. Reuse a cache entry only when its fingerprint and TTL remain valid.
7. Write the compiled Markdown packet under `.agent/state/contexts/` or the requested output path.
8. Report cache hit status, candidate paths, content size, and profile fingerprint.

# Decision rules

- Never include the full repository, full Graphify graph, or every skill by default.
- Treat candidate paths as navigation hints, not proof that omitted files are irrelevant.
- Invalidate context when a selected file, skill, profile, phase, or normalized task changes.
- Keep cached values local and derived; do not store secrets or raw credentials.
- Prefer deterministic parsing and hashing over another model call.
- Prune expired or old entries rather than deleting the whole cache routinely.

# Output

Return the compiled context path, cache key, cache-hit status, profile fingerprint, selected skills, candidate files, symbol inventory, and bounded Markdown content.

# Stop conditions

Stop when the context packet fits the configured character budget, its fingerprint is current, and no omitted data is being represented as confirmed irrelevant.
