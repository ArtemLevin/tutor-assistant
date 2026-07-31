# PR 45 validation

## Automated

- configuration default and validation;
- YAML persistence;
- WAV path without FFmpeg;
- M4A AAC 96 kbit/s command and FFprobe validation;
- MP3 libmp3lame 128 kbit/s command;
- atomic final-file replacement;
- WAV master preservation on encoding failure;
- session version 4 metadata;
- legacy session version 3 recovery to WAV;
- configurable recorder default;
- detailed-mode GUI selector contract.

## Manual Windows smoke

1. Start the GUI in detailed mode.
2. Confirm M4A is selected on a clean configuration.
3. Record 10–20 seconds and inspect `lesson.wav`, `lesson.m4a`, and `session.json`.
4. Repeat for MP3 and WAV.
5. Remove FFmpeg from `PATH` temporarily and confirm M4A/MP3 start is blocked before recording.
6. Restore a legacy session without `output_format` and confirm WAV output.
