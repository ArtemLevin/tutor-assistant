---
name: repository-context
description: >
  Build a minimal evidence-based map of a repository. Use before editing or
  reviewing code to locate entry points, invariant owners, direct dependencies,
  relevant tests, project commands, and unresolved unknowns without reading the whole repository.
---

# Purpose

Collect only the repository facts that can change the implementation decision.

# Inputs

- triage result;
- repository tree and metadata;
- root `graph.json` snapshot when present;
- task keywords, errors, symbols, or affected behavior.

# Workflow

1. If root `graph.json` exists, use it as portable navigation evidence; prefer `agentkit graph query` when the local CLI is available.
2. Inspect project manifests and the relevant subtree, not the entire repository.
3. Search for exact error text, public symbols, routes, commands, models, and tests.
4. Read signatures and call sites before full modules.
5. Trace the shortest path from entry point to the owner of the affected invariant.
6. Find the nearest existing test boundary and project verification commands.
7. Record confirmed facts, unknowns, and rejected assumptions in a context ledger.
8. Expand scope only when a decision depends on additional evidence.

# Decision rules

- Prefer symbol-level search, scoped graph queries, and targeted line ranges.
- Do not load the entire `graph.json` into context when a bounded query or selective parse can answer the question.
- Treat graph nodes and inferred edges as navigation hints; verify material conclusions against source files and tests.
- Ignore generated, vendored, cache, lock, and build files unless directly relevant.
- Do not infer ownership from filenames alone; verify call flow.
- Do not treat comments as truth when executable behavior contradicts them.
- Reuse previously inspected content when its hash and relevant symbols are unchanged.

# Output

Return entry points, invariant owners, direct dependencies, existing tests, commands, compatibility surfaces, unknowns, and inspected paths. Distinguish graph-derived hints from source-verified facts.

# Stop conditions

Stop when the likely change location and verification boundary are supported by evidence and further reading would not alter the plan.
