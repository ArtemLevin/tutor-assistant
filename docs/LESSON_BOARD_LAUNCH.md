# Lesson-bound TutorBoard launch (first integration slice)

## Scope and status

The first integration slice links a new standalone TutorBoard to an existing
desktop Lesson. It **does not** yet freeze a final board revision, export PDF, or
publish transcript and PDF together. Those are subsequent release slices.

The source of truth is the Lesson JSON payload in SQLite. Existing Lessons
without `board` load with a disabled default; no destructive migration is required.

## Configuration

In `config/app.yaml`:

```yaml
board:
  enabled: true
  base_url: https://YOUR-BOARD-DOMAIN
  expected_user_id: YOUR-AUTHENTICATED-BOARD-USER-ID
  expected_organization_id: YOUR-BOARD-ORGANIZATION-ID
```

The origin must be HTTPS (or loopback HTTP for development), without embedded
credentials, paths or query parameters. The expected user and organization IDs
must match the JSON fields returned by an authenticated `GET /api/v1/boards/context`
request in the browser. A different signed-in teacher cannot start this lesson board. Use a board deployment where the teacher
can authenticate at `/login`. The per-lesson opt-in appears in both Quick Lesson
and the detailed recording form and is synchronized between them.

## Data flow

1. `StartRecordingUseCase` creates a unique Lesson, persists its opt-in
   `board.status=pending`, and starts recording.
2. After recording has started successfully, `LocalBoardLaunch` opens a
   loopback listener on `127.0.0.1` and launches
   `https://YOUR-BOARD-DOMAIN/boards#ta-start=<base64url>`.
3. The browser validates the strictly typed, one-time launch context, including
   the pinned user and organization IDs, in the
   URL **fragment** and removes it from browser history.
4. Before issuing any write, the browser verifies the one-time nonce against
   `GET http://127.0.0.1:<port>/prepare?nonce=<nonce>` with a CORS
   response scoped to the configured TutorBoard origin. The desktop listener
   refuses other origins.
5. The browser compares the authenticated teacher/organization context with the
   pinned values before any write. The already-authenticated browser sends the usual
   CSRF-protected `POST /api/v1/boards`. Tutor Assistant never stores Board API
   session cookies, a teacher password, or a privileged API token.
6. Board API atomically creates or reuses one board per organization and client
   lesson ID (`clientLessonId`). A retry or browser crash cannot create duplicates.
7. The browser navigates to the one-time loopback
   `/complete?nonce=<nonce>&boardId=<boardId>` callback. The desktop
   atomically persists the board association using the current recording
   lease, then redirects the browser to `/b/<boardId>#/board`.
8. On successful recording finalization, the Lesson receives
   `board.recording_finished=true`. A linked board becomes
   `board.pdf_status=pending` for the later PDF worker.

The local callback and board UUID are not proof of a finalized revision.
All later exports must recheck authenticated board access and the pinned
server-side revision.

## Failure and recovery

- A failed browser launch never aborts an active audio recording.
- A failed or delayed browser callback leaves a durable pending/failed
  association for diagnostics; a later feature slice will provide a
  recovery/relink UI.
- Replaying the same board ID is idempotent; a different board ID for
  the same lesson is rejected.
- A pending callback remains alive for at most 30 minutes and is closed
  during application shutdown. Start and stop do not depend on the browser.
- React StrictMode and browser reloads must not create duplicate boards
  for the same pending lesson; the browser deduplicates in-flight creation.
- If TutorBoard requires teacher login, sign in in a separate tab and
  use the existing `Проверить ещё раз` action while the launch tab remains open.
- Chrome's local-network-access or firewall policy may restrict the
  `https → http://127.0.0.1` challenge. Such a failure must be visible,
  and production Windows/Chromium QA is required before enabling by default.

## Safety and release gates

Tests must cover: default-off legacy loading, URI and ID validation,
invalid/expired callback challenge, origin restrictions, one-time
completion, duplicate callback, cross-lesson board substitution,
successful association during an active recording lease, late callback
after recording stop, shutdown, and no regression of ASR/publication.

After a desktop restart, orphaned pending handoffs are marked failed, and
successfully recorded lessons missing a board-finished marker are reconciled.
The retry controls on Quick Lesson and the detailed lesson page can reopen
failed lesson boards without changing their durable client lesson key.

Rollout order: deploy tutor-assistant-web support for `clientLessonId` and its
database migration before deploying this TutorBoard build and the Windows app.

The next stage adds an immutable board revision, PDF rendering and a
durable two-file publisher with per-artifact checksums and exact remote
verification. Until that exists, `pdf_status=pending` is a planned
downstream operation, not an assertion that an export has started.
