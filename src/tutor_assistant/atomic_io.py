from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from time import sleep

ATOMIC_WRITE_ATTEMPTS = 6
ATOMIC_WRITE_RETRY_SECONDS = 0.05


def _write_text_durable(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())


def atomic_write_text(path: Path, content: str) -> None:
    """Durably replace a text file, tolerating transient Windows locks."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

        last_error: PermissionError | None = None
        for attempt in range(ATOMIC_WRITE_ATTEMPTS):
            try:
                temporary.replace(path)
                return
            except PermissionError as exc:
                last_error = exc
                if attempt + 1 < ATOMIC_WRITE_ATTEMPTS:
                    sleep(ATOMIC_WRITE_RETRY_SECONDS * (2**attempt))

        logging.warning(
            "Не удалось атомарно заменить %s после %s попыток (%s); использую прямую durable-запись",
            path,
            ATOMIC_WRITE_ATTEMPTS,
            last_error,
        )
        _write_text_durable(path, content)
    finally:
        temporary.unlink(missing_ok=True)
