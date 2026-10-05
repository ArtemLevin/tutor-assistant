# Automatic transcript publication

**Status:** implemented in PR #116, merged to `main` on 5 October 2026.

This document describes the production contract for the opt-in automatic lesson mode. It is not
a future implementation plan.

## User contract

The default lesson mode remains manual. Before recording starts, the teacher may explicitly enable:

```text
Автоматически транскрибировать и отправить на GitHub
```

The choice is persisted on that Lesson as
`LessonProcessingMode.AUTO_TRANSCRIPT_GITHUB`. Older lessons and lessons without the field load as
`MANUAL`.

Manual mode keeps the existing approval contract:

```text
recording
→ stop/finalize
→ optional/local ASR
→ REVIEW_REQUIRED
→ teacher review
→ READY
→ manual publication
```

Automatic mode uses a separate authorization path:

```text
recording
→ stop/finalize
→ persistent transcription queue
→ local ASR
→ semantic ASR result validation
→ immutable automatic-transcription revision
→ persistent automatic publication queue
→ verified GitHub publication
→ PUBLISHED
```

`READY` continues to mean **teacher-approved transcript**. Automatic machine output is never marked
as teacher-approved only to reuse the manual publisher.

## Durable data flow

Downstream processing starts only after recording finalization returns `RECORDED` with a valid
mixed audio file. `RECOVERY_REQUIRED` and recording-finalization failure keep the existing
audio-first recovery semantics and do not start automatic publication.

After ASR, the pipeline first validates that the result contains at least one meaningful speech
segment and that the cleaned transcript contains a letter or digit. This check has no minimum lesson
duration: a short meaningful recording is valid, while a zero-segment or punctuation-only result
fails transcription.

Only after that validation does the pipeline canonicalize the cleaned transcript, save an immutable
transcript revision to SQLite with `created_by="automatic-transcription"`, and create a durable
`automatic_publication_jobs` intent containing:

- lesson id;
- revision number;
- transcript SHA-256;
- target repository path;
- queue status, attempts, error and next retry time.

The immutable tuple `revision_number + content_sha256 + repository_path` cannot be silently changed
for an existing job.

The Git repository target is resolved independently from the existing Pages/materials repository.

Preferred configuration:

```yaml
repository:
  students_repo: ../students-26-27
  repository_full_name: owner/public-students-pages

automatic_transcript_repository:
  students_repo: ../students-transcripts
  remote: origin
  push: true
  repository_full_name: owner/private-students-transcripts
```

`repository` remains the target for existing manual/web/LaTeX workflows.
`automatic_transcript_repository` is used only by `AUTO_TRANSCRIPT_GITHUB`.
If the dedicated block is absent, older configurations remain valid: automatic publication falls
back to `repository`, but the private-repository policy still applies and therefore a public Pages
repository is rejected.

The publication path inside the private repository is:

```text
<student.repository_folder>/transcript/DD.MM.YY.txt
```

The date comes from `lesson.lesson_date`, not from wall-clock time at publication.

## Publication safety

The dedicated automatic transcript repository must be a local Git checkout whose configured remote
matches `automatic_transcript_repository.repository_full_name`; the GitHub repository must be
PRIVATE. A public repository is blocked even if it is otherwise a valid Git remote.

Automatic publication reads the exact persisted automatic transcript revision from SQLite,
verifies its SHA-256, and rejects semantically empty historical revisions before Git access.
Startup reconciliation does not create publication work from an empty automatic revision; an
existing unpublished empty intent is blocked until transcription succeeds.

A successful retranscription may repair a legacy unpublished intent that points specifically to an
older empty automatic revision. The repair is transactional, compare-and-swap guarded, verifies both
old and replacement automatic revisions inside the same SQLite transaction, resets retry state, and
is forbidden once a live intent is `running` or `published`.

When a durable publication job already exists, its immutable
`revision_number + content_sha256 + repository_path` tuple is authoritative. A newer automatic
revision must not silently retarget or block a valid existing job. Newer revisions participate only
when creating a missing intent or repairing a job that is proven to reference an older empty
automatic revision.

The Git transport preserves the existing publication safety boundary:

1. verify configured repository identity/policy;
2. fetch the configured remote target branch;
3. capture the expected remote commit;
4. use an isolated detached worktree;
5. write only the expected transcript path;
6. verify staged/outgoing transcript-only egress;
7. verify Git blob SHA-256;
8. create the publication commit;
9. push with `--force-with-lease` against the captured remote commit;
10. verify the resulting remote commit/content;
11. persist the publication result and transition the Lesson to `PUBLISHED`.

Audio, JSON, manifests, TEX/PDF, logs and other local lesson artifacts are not part of the automatic
Git egress.

If the target path already exists with another content SHA-256, publication fails closed with
`conflict`; automatic overwrite is forbidden.

## Queue and retry semantics

Automatic publication is single-worker and persistent. Runtime states are:

| State | Meaning |
| --- | --- |
| `waiting` | durable job is ready to run |
| `running` | publication worker owns the current attempt |
| `retry_required` | transient Git failure; retry is scheduled |
| `published` | remote publication has been verified |
| `blocked` | configuration/security/runtime prerequisite must be fixed before explicit retry |
| `conflict` | remote target contains different content; no automatic retry |

Transient `GitError` uses bounded retry delays:

```text
30s → 120s → 600s → 1800s
```

After the retry budget is exhausted, the job becomes `blocked`. A
`PublicationBlockedError` is blocked immediately. A `PublicationConflictError` becomes terminal
`conflict`.

A blocked job can be retried explicitly after the underlying cause is corrected. Conflict is not
made retryable automatically because that could hide or overwrite a remote data collision.

## Startup and crash recovery

At startup the pipeline reconciles automatic publication intents against durable lessons and
automatic transcript revisions.

Important recovery rules:

- a missing publication intent can be recreated only when the latest durable automatic revision is meaningful;
- an existing job is reconciled against the exact automatic revision pinned by its immutable tuple,
  not against an unrelated newer revision;
- a persisted meaningful `running` publication is restored as retryable work rather than assumed
  successful;
- at process startup only, a stale `running` job pinned to an empty automatic revision is quarantined
  before it can be repaired to a newer meaningful revision or left `blocked`;
- runtime reconciliation never reclassifies a live `running` publication worker;
- an already remotely verified/persisted publication is reconciled to `published`;
- missing pinned revisions and immutable path mismatches are surfaced as conflict;
- retry/restart does not create a second automatic transcript revision when durable ASR artifacts
  already exist.

## Shutdown and concurrency

Transcription and publication use separate background workers. Starting the next lesson does not
require waiting for a previous automatic transcript to publish.

Safe shutdown treats an active publication worker as a drain barrier. No new publication is pumped
after shutdown begins, the retry timer is stopped, and waiting/retry jobs remain in SQLite for the
next application start.

## Operational UI

Automatic publication jobs are shown in the existing background-processing UI together with
transcription work. The UI exposes waiting/running/retry/blocked/conflict/published state and the
stored error details.

For `blocked`, fix the configuration/access/policy issue and retry explicitly. For `conflict`,
inspect the existing remote transcript and resolve the collision manually; the application does not
overwrite it.

## Regression contract

The production regression suite covers:

- backward-compatible manual/default processing mode;
- automatic mode forcing post-recording transcription;
- empty ASR rejection without a duration threshold;
- immutable automatic revision creation and ASR reconciliation;
- pinned-revision recovery, startup quarantine and controlled repair of legacy empty unpublished publication intents;
- durable queue migration/storage/restart behavior;
- retry/backoff, block and conflict classification;
- publication worker transport behavior;
- shutdown barriers;
- transcript-only Git egress and SHA validation;
- end-to-end `stop → ASR → SQLite revision → publication queue → verified Git publication` against
  a real local bare Git remote.

PR #116 passed the full `Release 1.0 Gate`, Windows full suite, privacy history,
accessibility/scaling and Python 3.13/3.14 compatibility checks before merge.
