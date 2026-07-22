# Token and context budget

The goal is not minimum token use. The goal is minimum token use per correctly completed task.

## Default budgets

| Mode | Initial files | Planning | Implementation cycles | Review cycles |
|---|---:|---:|---:|---:|
| fast | 3–5 | none or <= 3 steps | 1 | inline |
| standard | 5–12 | <= 10 steps | <= 2 | 1 |
| deep | dependency-driven | explicit | <= 3 with evidence | specialist + adversarial |

These are behavioral limits, not hard repository limits. Exceed them only after recording why additional context changes a decision.

## Progressive disclosure

1. Keep the skill registry to `name` and activation-focused `description`.
2. Load a complete `SKILL.md` only after triage selects it.
3. Load references or scripts only when the workflow reaches the relevant decision.
4. Pass structured deltas between stages rather than full transcripts.

## Repository reading policy

Prefer:

- tree before file;
- symbol before module;
- direct dependency before transitive dependency;
- nearest tests before all tests;
- diff before complete changed files during review;
- summarized logs before raw logs.

Avoid:

- lock files unless dependency resolution matters;
- generated assets;
- vendored code;
- full build logs when the first failure is sufficient;
- rereading files whose hash and relevant symbols are unchanged.

## Context ledger

Maintain:

```yaml
confirmed: []
unknowns: []
rejected_assumptions: []
inspected_files: {}
changed_symbols: []
executed_checks: []
```

Do not store speculative prose as confirmed context.

## Iteration policy

A new implementation cycle requires at least one of:

- a failing check with actionable evidence;
- a P0/P1 review finding;
- a newly discovered contract;
- invalidation of a material assumption.

Do not spend another cycle on style-only P3 findings after completion criteria pass.
