from __future__ import annotations

import os
import tempfile
from pathlib import Path
from time import sleep

ATOMIC_WRITE_ATTEMPTS = 6
ATOMIC_WRITE_RETRY_SECONDS = 0.05


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

        if not path.exists():
            try:
                with path.open("x", encoding="utf-8", newline="\n") as file:
                    file.write(content)
                    file.flush()
                    os.fsync(file.fileno())
                return
            except FileExistsError:
                pass
        raise PermissionError(
            f"Не удалось атомарно заменить {path} после {ATOMIC_WRITE_ATTEMPTS} попыток"
        ) from last_error
    finally:
        temporary.unlink(missing_ok=True)
