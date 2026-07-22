---
name: database-review
description: >
  Review schema, ORM, SQL, transactions, constraints, indexes, migrations,
  backfills, and persistence compatibility. Use when a change reads or writes
  durable state or modifies database structure or transactional behavior.
---

# Purpose

Protect data integrity, compatibility, and operational safety.

# Inputs

- schema and migration history;
- changed queries and transaction boundaries;
- data volume and deployment model;
- rollback or forward-recovery requirements.

# Workflow

1. Identify invariants that belong in application code versus database constraints.
2. Trace transaction boundaries and failure paths.
3. Check nullability, uniqueness, foreign keys, defaults, and concurrent writes.
4. Review query shape for missing indexes, N+1 behavior, locks, and unbounded scans.
5. For migrations, assess expand/contract compatibility, table locking, backfill cost, and mixed-version deployment.
6. Verify upgrade and supported recovery path with real tooling where possible.
7. Check that tests use a boundary capable of exposing the relevant database behavior.

# Decision rules

- Prefer durable constraints for cross-process invariants when appropriate.
- Do not assume ORM validation prevents concurrent violations.
- Avoid destructive migration steps before all deployed code stops depending on old data.
- Do not claim rollback is safe when data transformation is irreversible.
- Do not optimize queries without explaining the expected access path.

# Output

Return integrity findings, migration risk, query risk, compatibility requirements, executed checks, and recovery strategy.

# Stop conditions

Stop when durable invariants, transaction semantics, deployment compatibility, and recovery limitations are explicit.
