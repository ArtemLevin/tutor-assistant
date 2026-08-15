# PR 47 — Audio recovery and encoding integrity

## Production contracts

1. Every `DualRecorder` receives its output format explicitly when the instance is created.
2. WAV chunks and `lesson.wav` remain the recovery source of truth.
3. The low-level recorder performs only WAV recovery. Package imports must not replace or monkey-patch `recorder.recover_recording` with format-aware recovery.
4. GUI recovery reads `output_format` from `session.json` and recreates the selected delivery file from the WAV master.
5. M4A requires the FFmpeg `aac` encoder; MP3 requires `libmp3lame`.
6. Encoded output is accepted only when FFprobe confirms:
   - the expected codec;
   - duration within `max(1 second, 2%)` of the WAV master;
   - identical sample rate;
   - identical channel count;
   - a readable, non-empty output file.
7. The configured bitrate is an encoder target, not an integrity invariant. FFprobe's reported average bitrate is diagnostic metadata and may legitimately be lower for silence or low-complexity audio.
8. Delivery encoding accepts only a WAV master. An already encoded M4A/MP3 must never be passed through a second lossy transcode.
9. Callback age starts at recorder startup. A device that produces no first callback reaches the existing GUI timeout and triggers controlled recording shutdown.
10. Failed encoding keeps `lesson.wav`, marks `session.json` as `encoding_failed`, and remains discoverable and recoverable.

## Manual Windows verification

1. Select M4A, record a short lesson with normal speech, stop, and confirm `lesson.m4a`, `lesson.wav`, and `session.json`.
2. Repeat M4A with long pauses or mostly silence. A low average bitrate reported by FFprobe must not reject an otherwise valid file.
3. Repeat with MP3 and WAV.
4. Interrupt the app during or immediately before final encoding, restart, accept recovery, and confirm that the format from `session.json` is recreated from `lesson.wav`.
5. Confirm that recovery of an `encoding_failed` session is offered automatically.
6. Run with an FFmpeg build without `libmp3lame`; MP3 preflight must fail before recording starts and mention the missing encoder.
7. Disable or disconnect one input device before its first callback; the existing device timeout must stop the recording and preserve recoverable chunks.
