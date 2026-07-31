# PR 45 release notes

Detailed mode now offers M4A, MP3, and WAV delivery formats. M4A with AAC at 96 kbit/s is the default. MP3 uses 128 kbit/s. WAV remains PCM16.

Tutor Assistant keeps WAV chunks and a WAV master for reliable recovery. Compressed outputs require FFmpeg and FFprobe. The application verifies codec and duration before publishing the final local file.
