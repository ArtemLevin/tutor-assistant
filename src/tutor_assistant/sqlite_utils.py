from __future__ import annotations

import sqlite3


class ClosingConnection(sqlite3.Connection):
    """SQLite connection whose context manager also releases the file handle."""

    def __exit__(self, exc_type, exc, traceback) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc, traceback))
        finally:
            self.close()
