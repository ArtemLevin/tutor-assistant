# Tool permissions

Grant the smallest tool set required by each skill.

| Skill | Repository read | Write | Shell | Network |
|---|---:|---:|---:|---:|
| task-triage | metadata/tree | no | status/tree | no |
| repository-context | scoped | no | search/git | no |
| requirements-contract | scoped | no | no | no |
| change-planner | scoped | no | no | no |
| architecture-guard | scoped | no | dependency inspection | normally no |
| engineering-balance | scoped | no | optional metrics | no |
| implementation | scoped | approved files | formatter | normally no |
| verification-router | diff/tests/config | no | inspect commands | no |
| risk-based-testing | code/tests | tests only | test runner | no |
| adversarial-review | diff/relevant code | no | read-only checks | no |
| security-review | security scope | no | scanners | advisory lookup only |
| concurrency-review | concurrency scope | no | tests/profilers | no |
| database-review | schema/queries | no | migration/test tools | no |
| intent-documentation | changed scope | docs/comments | formatter | no |
| delivery-summary | diff/results | no | git status/diff | no |

## Destructive actions

The following require explicit user intent or a previously approved plan:

- deleting files or data;
- force-pushing or rewriting history;
- applying production migrations;
- deploying to production;
- rotating or exposing credentials;
- merging pull requests;
- changing branch protection;
- running commands outside the repository workspace.

## Evidence boundary

Read-only analysis may propose commands. A skill may claim a result only after the authorized tool executed it and returned success.
