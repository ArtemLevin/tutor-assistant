# Review severity model

## P0 — Critical

The change can cause immediate severe harm or is unusable:

- data loss or corruption;
- authentication or authorization bypass;
- secret disclosure or remote code execution;
- irreversible destructive migration;
- deadlock or system-wide outage;
- code does not build, import, or start in the required path.

P0 blocks delivery.

## P1 — High

The change materially violates the contract or creates a likely production regression:

- acceptance criterion is not met;
- race condition, duplicate side effect, or broken idempotency;
- transaction leaves inconsistent state;
- public API incompatibility not approved;
- error handling hides a common failure;
- test passes without exercising the intended behavior.

P1 blocks delivery.

## P2 — Medium

The change works but has a concrete maintainability or coverage defect:

- duplicated logic with an existing owner;
- unnecessary coupling;
- misleading name or comment;
- missing test for an identified medium-risk branch;
- avoidable complexity that increases future error probability.

P2 should be fixed when local and low-cost; otherwise disclose it.

## P3 — Low

Optional improvement without a demonstrated correctness or maintenance impact:

- stylistic preference;
- speculative optimization;
- alternative naming of similar clarity;
- hypothetical abstraction.

P3 never extends the task after the completion gate passes.

## Finding requirements

Every blocking finding must contain:

- severity;
- exact location;
- violated contract or invariant;
- concrete failure scenario;
- evidence from code or executed checks;
- smallest safe fix.

A concern without a plausible failure path is not a blocking finding.
