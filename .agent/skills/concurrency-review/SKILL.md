---
name: concurrency-review
description: >
  Review async code, threads, workers, queues, locks, retries, shared state,
  cancellation, idempotency, and distributed coordination. Use when operation
  interleavings or repeated delivery can change correctness.
---

# Purpose

Expose race conditions and partial-failure behavior that linear code review misses.

# Inputs

- changed concurrent flow and state transitions;
- delivery guarantees and process topology;
- transaction boundaries;
- retry, timeout, and cancellation behavior.

# Workflow

1. Enumerate shared state and all writers.
2. Model at least two concurrent or repeated executions.
3. Identify atomicity boundaries and check-then-act sequences.
4. Check idempotency keys, deduplication, retry safety, and terminal states.
5. Examine cancellation, timeout, shutdown, and partial-success paths.
6. Verify lock scope, ordering, ownership, and multi-process validity.
7. Require a concurrency test only when it can deterministically protect a real interleaving or invariant.

# Decision rules

- A process-local lock cannot protect a distributed invariant.
- At-least-once delivery requires idempotent effects or durable deduplication.
- Database state and message publication are not atomic without an explicit mechanism.
- Do not “fix” races with sleeps.
- Treat cancellation as a normal control path in async code.

# Output

Return state-machine assumptions, dangerous interleavings, findings, smallest fixes, and untested concurrency risks.

# Stop conditions

Stop when shared-state ownership, atomicity, retry semantics, and cancellation behavior are explicit and blocking interleavings are addressed.
