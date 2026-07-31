from __future__ import annotations

import argparse
from pathlib import Path

from .output import recover_recording


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Восстановить аудио из завершённых WAV-чанков Tutor Assistant"
    )
    parser.add_argument("recording_dir", type=Path)
    parser.add_argument(
        "--format",
        choices=("m4a", "mp3", "wav"),
        dest="output_format",
        help="Переопределить формат из session.json",
    )
    args = parser.parse_args()
    result = recover_recording(args.recording_dir, args.output_format)
    print(result.mixed_file)


if __name__ == "__main__":
    main()
