# Lesson journal closeout

## Purpose

The closeout layer completes the operational lesson workflow after a lesson has ended. A concrete schedule occurrence can now record attendance, a local teacher note and the exact closeout time before its status is moved to `completed`.

## Storage boundary

Closeout data lives in the local CRM SQLite database in `crm_lesson_closeout` and is keyed by `occurrence_id`.

- `attendance` stores the explicit attendance state.
- `teacher_note_ciphertext` stores the teacher note encoded through the CRM secret codec.
- `closed_at` marks a completed pedagogical closeout.
- `updated_at` records the latest administrative change.

The closeout table has no publication-layer dependency. `ScheduledLesson` and publication payloads do not expose the teacher note or attendance fields.

## Attendance states

- `unknown` — attendance has not been recorded.
- `present` — student attended.
- `late` — student attended late.
- `no_show` — student did not attend.
- `excused` — absence was agreed in advance.

## Recurring lessons

Reading and filtering recurring lessons does not create occurrence rows. Saving attendance, saving a teacher note or completing the lesson materializes only the selected recurring date. Adjacent dates remain virtual until their own administrative action occurs.

## Closeout transaction

`LessonCloseoutService.close_lesson()` performs materialization, the occurrence status update and closeout persistence within one CRM database transaction. A lesson can be completed only after its end time and only after attendance has been selected.

## Undo

Before attendance or closeout mutations the UI captures `LessonCloseoutSnapshot`. The snapshot contains the prior occurrence status and the complete previous closeout state. Undo restores those values exactly. A materialized recurring occurrence may remain materialized after Undo; its administrative state is restored.

## Journal behavior

The journal adds:

- attendance filtering;
- a `Незавершённые` smart view;
- an unfinished summary indicator;
- attendance status chips;
- a local teacher-note editor with dirty state;
- `Ctrl+S` to save the closeout draft;
- `Ctrl+Enter` to complete the selected lesson;
- `F3` to focus attendance;
- existing `Ctrl+Z` support for attendance and closeout changes.

A past active lesson is considered unfinished when its status is not `completed`, closeout metadata is missing, `closed_at` is missing, or attendance is still `unknown`.

## Accessibility

Attendance and closeout controls expose accessible names and descriptions. Attendance meaning is always expressed in text in addition to visual tone. Keyboard focus remains visible and all closeout actions are reachable without a pointing device.

## Production gate

The closeout change must retain the existing repository gates:

- Ruff and Python compilation;
- privacy and secret-history checks;
- full pytest on Python 3.11, 3.12, 3.13 and 3.14;
- Windows accessibility scaling at 100%, 125%, 150% and 200%;
- closeout-specific 1024×720 GUI geometry smoke.
