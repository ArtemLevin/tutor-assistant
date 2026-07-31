# PR 47 — Audio recovery and encoding integrity

## Production contracts

1. Every `DualRecorder` receives its output format explicitly when the instance is created.
2. WAV chunks and `lesson.wav` remain the recovery source of truth.
3. GUI recovery reads `output_format` from `session.json` and recreates the selected delivery file.
4. M4A requires the FFmpeg `aac` encoder; MP3 requires `libmp3lame`.
5. Encoded output is accepted only when FFprobe confirms:
   - the expected codec;
   - duration within `max(1 second, 2%)` of the WAV master;
   - identical sample rate;
   - identical channel count;
   - bitrate within the bounded profile tolerance when FFprobe reports bitrate.
6. Callback age starts at recorder startup. A device that produces no first callback reaches the existing GUI timeout and triggers controlled recording shutdown.
7. Failed encoding keeps `lesson.wav`, marks `session.json` as `encoding_failed`, and remains recoverable.

## Manual Windows verification

1. Select M4A, record a short lesson, stop, and confirm `lesson.m4a`, `lesson.wav`, and `session.json`.
2. Repeat with MP3 and WAV.
3. Interrupt the app during or immediately before final encoding, restart, accept recovery, and confirm that the format from `session.json` is recreated.
4. Run with an FFmpeg build without `libmp3lame`; MP3 preflight must fail before recording starts and mention the missing encoder.
5. Disable or disconnect one input device before its first callback; the existing device timeout must stop the recording and preserve recoverable chunks.
