# PR 48 — Library retranscription and readable audio filenames

## Archive transcription

The lesson-content dialog exposes `Транскрибировать аудио` for every available indexed audio row. This includes the delivery file, `lesson.wav`, microphone/system tracks and imported audio formats.

The selected path is submitted to the existing durable single-worker transcription queue. Stable archive states are moved to `RECORDED` before processing. Recording, transcription, PDF compilation and material generation block a second transcription operation for the same lesson.

When a confirmed transcript already exists, the UI asks for confirmation. The current confirmed revision remains in SQLite history until the teacher approves the new Whisper result.

## Audio filename contract

User-facing delivery audio uses:

```text
<student>_<YYYY-MM-DD>.<extension>
```

Example:

```text
Иван_Петров_2026-07-31.m4a
```

Cyrillic is preserved. Whitespace becomes underscores. Windows-reserved characters and device names are normalized.

The recovery master remains `lesson.wav`. M4A and MP3 delivery files are atomically renamed after verification. WAV delivery is copied atomically to the readable filename, leaving `lesson.wav` available for recovery and future transcoding. `session.json` records both `master_file` and the readable `output_file`.

## Manual Windows verification

1. Record lessons in M4A, MP3 and WAV and confirm the readable delivery filename.
2. Confirm `lesson.wav` remains beside every delivery file.
3. Open `Материалы ученика`, select each available audio row and start transcription.
4. Repeat the operation for an already published lesson and confirm it enters the background queue.
5. Confirm the previous approved transcript remains available in revision history until the new result is approved.
