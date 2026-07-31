# PR 46: publication integrity and unified transcript egress

## Security contract

A successful publication means that the exact transcript commit is present on the configured private GitHub repository and on the `main` branch.

The publication path is:

```text
<student-folder>/lessons/<date>_<topic>__<lesson-id-prefix>/transcript.txt
```

Only one UTF-8 `transcript.txt` is allowed. Audio, lesson metadata, segments, manifests, reports, LaTeX, PDF, previews and logs remain local.

## Verified flow

1. Validate the manually approved transcript.
2. Read the actual push URL from the configured Git remote.
3. Parse the GitHub owner/repository identity.
4. Require equality with `repository.repository_full_name`.
5. Require PRIVATE repository visibility.
6. Fetch and record the current remote `main` SHA.
7. Create an isolated detached worktree from that exact SHA.
8. Stage and commit exactly one `transcript.txt`.
9. Perform a normal fast-forward push.
10. Read the remote branch SHA through `git ls-remote`.
11. Report success only when the remote SHA equals the local commit SHA.

## Crash recovery

Publication operations are recorded in `publication.sqlite3` beside the local lesson archive. The journal stores the expected remote SHA, local commit SHA, content SHA-256 and operation status.

On retry, Tutor Assistant reconciles an unfinished operation:

- remote equals the local commit: finish the operation idempotently;
- remote equals the expected base: mark the previous attempt failed and permit retry;
- remote has a third SHA: stop with a conflict;
- remote is unavailable after push: preserve an indeterminate operation for later reconciliation.

## Operational defaults

- production publication requires `repository.push: true`;
- remote LaTeX monitoring is opt-in;
- PDF publication is disabled;
- the legacy LaTeX route must never be used as a general Git egress path.
