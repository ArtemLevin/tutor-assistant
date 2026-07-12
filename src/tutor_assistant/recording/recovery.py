from __future__ import annotations

import argparse
from pathlib import Path

from .recorder import recover_recording


def main() -> None:
    parser = argparse.ArgumentParser(description="Восстановить WAV из завершённых чанков Tutor Assistant")
    parser.add_argument("recording_dir", type=Path)
    args = parser.parse_args()
    result = recover_recording(args.recording_dir)
    print(result.mixed_file)


if __name__ == "__main__":
    main()
