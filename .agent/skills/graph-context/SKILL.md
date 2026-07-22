---
name: graph-context
description: >
  Use when a task depends on code relationships, architecture, callers, dependencies, tests, or impact analysis and a Graphify graph can reduce raw file reads.
---

# Purpose

Use Graphify as a query-first navigation index and return the smallest code subgraph needed for the task.

# Inputs

- user task
- current repository HEAD
- `graphify-out/graph.json` when available
- configured query budget

# Workflow

1. Check graph freshness and update incrementally when source files changed.
2. Ask one narrow question covering entry points, direct dependencies, callers, and related tests.
3. Keep the result within the configured token budget.
4. Separate `EXTRACTED`, `INFERRED`, and ambiguous relationships.
5. Confirm critical relationships in source code before implementation.

# Decision rules

- Never load `graph.json` in full when a scoped query is sufficient.
- Treat Graphify as navigation evidence, not proof of runtime behavior.
- Recheck dynamic imports, callbacks, dependency injection, ORM behavior, and concurrency in source and tests.
- Stop after the initial query unless a specific unresolved dependency justifies another one.

# Output

Return entry points, direct dependencies, callers, related tests, confidence labels, and unresolved graph gaps.

# Stop conditions

Stop when the initial file and symbol set is small enough for direct source inspection.
