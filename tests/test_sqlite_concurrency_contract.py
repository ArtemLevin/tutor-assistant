from __future__ import annotations

from pathlib import Path

from tutor_assistant.content.repository import StudentContentRepository
from tutor_assistant.crm import CrmStore
from tutor_assistant.domain import Student
from tutor_assistant.store import LessonStore


def _assert_connection_contract(connection) -> None:
    assert str(connection.execute("PRAGMA journal_mode").fetchone()[0]).casefold() == "wal"
    assert int(connection.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
    assert int(connection.execute("PRAGMA busy_timeout").fetchone()[0]) >= 10_000
    assert str(connection.execute("PRAGMA synchronous").fetchone()[0]) in {"1", "2"}


def test_all_primary_sqlite_entrypoints_share_safety_pragmas(tmp_path: Path) -> None:
    path = tmp_path / "assistant.sqlite3"
    lesson_store = LessonStore(path)
    repository = StudentContentRepository(path)
    crm = CrmStore(path)

    with lesson_store.connect() as db:
        _assert_connection_contract(db)
    with repository.connect() as db:
        _assert_connection_contract(db)
    with crm.connect() as db:
        _assert_connection_contract(db)


def test_wal_keeps_crm_reader_available_during_uncommitted_writer(tmp_path: Path) -> None:
    store = CrmStore(tmp_path / "assistant.sqlite3")
    store.sync_students([Student(id="sqlite-reader", full_name="SQLite Reader")])

    with store.connect() as writer:
        writer.execute(
            "UPDATE crm_students SET goal='uncommitted' WHERE id='sqlite-reader'"
        )
        with store.connect() as reader:
            value = reader.execute(
                "SELECT goal FROM crm_students WHERE id='sqlite-reader'"
            ).fetchone()[0]

        assert value == ""

    assert store.get_student("sqlite-reader").goal == "uncommitted"
