"""Canonical ownership and filesystem-boundary checks for destructive operations."""

from __future__ import annotations

import re
import stat
from pathlib import Path

from .models import (
    ContentOperation,
    ContentOperationKind,
    TrashEntry,
    TrashState,
    _validate_lesson_id,
)


class ContentPathError(ValueError):
    """An on-disk or persisted path cannot be trusted for content deletion."""


_IMPORT_STAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*-[0-9a-f]{32}")
_OPERATION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


class ManagedPathPolicy:
    """Fail closed for unsafe links and paths outside the canonical managed layout."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace.resolve()

    def lesson(self, lesson_id: str) -> Path:
        return self.workspace / "lessons" / _validate_lesson_id(lesson_id)

    def trash(self, lesson_id: str) -> Path:
        return self.workspace / "trash" / "lessons" / _validate_lesson_id(lesson_id)

    def staging(self, operation_id: str) -> Path:
        if not _OPERATION_ID.fullmatch(operation_id):
            raise ContentPathError("Некорректный идентификатор операции очистки")
        return self.workspace / ".trash-purge" / operation_id

    @staticmethod
    def _unsafe_reparse(path: Path, metadata: stat.stat_result) -> bool:
        attributes = getattr(metadata, "st_file_attributes", 0)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        return (
            stat.S_ISLNK(metadata.st_mode)
            or bool(reparse_flag and attributes & reparse_flag)
            or (hasattr(path, "is_junction") and path.is_junction())
        )

    def validate_path(self, path: Path) -> None:
        """Inspect every existing component with lstat, never trusting resolve alone."""
        try:
            relative = path.relative_to(self.workspace)
        except ValueError as exc:
            raise ContentPathError("Путь находится вне рабочего каталога") from exc
        current = self.workspace
        for index, part in enumerate(relative.parts):
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                continue
            if self._unsafe_reparse(current, metadata):
                raise ContentPathError(f"Ссылка или reparse point в пути: {current}")
            if index < len(relative.parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
                raise ContentPathError(f"Родительский путь не является каталогом: {current}")
        if not path.resolve().is_relative_to(self.workspace):
            raise ContentPathError("Путь выходит за пределы рабочего каталога")

    def validate_tree(self, root: Path) -> None:
        """Reject nested links and junctions before directory moves or rmtree."""
        self.validate_path(root)
        if not root.exists():
            return
        if not root.is_dir():
            raise ContentPathError(f"Ожидается управляемый каталог: {root}")
        pending = [root]
        while pending:
            directory = pending.pop()
            for child in directory.iterdir():
                self.validate_path(child)
                if child.is_dir():
                    pending.append(child)

    def validate_trash_entry(self, entry: TrashEntry, *, operation_id: str | None = None) -> None:
        lesson = self.lesson(entry.lesson_id)
        trash = self.trash(entry.lesson_id)
        if (
            entry.original_relative_path != lesson.relative_to(self.workspace).as_posix()
            or entry.trash_relative_path != trash.relative_to(self.workspace).as_posix()
        ):
            raise ContentPathError("Каталог корзины не соответствует управляемому занятию")
        if entry.staging_relative_path is not None:
            if entry.state != TrashState.PURGING:
                raise ContentPathError("Неожиданный staging-путь в состоянии корзины")
            staging = self.workspace / entry.staging_relative_path
            if (
                len(staging.relative_to(self.workspace).parts) != 2
                or staging.parent != self.workspace / ".trash-purge"
                or not _OPERATION_ID.fullmatch(staging.name)
                or (operation_id is not None and staging != self.staging(operation_id))
            ):
                raise ContentPathError("Некорректный staging-путь корзины")
            self.validate_path(staging)
        elif entry.state == TrashState.PURGING:
            raise ContentPathError("Отсутствует staging-путь очистки")
        for target in (lesson, trash):
            self.validate_tree(target)

    def validate_operation(self, operation: ContentOperation) -> None:
        lesson = self.lesson(operation.lesson_id).relative_to(self.workspace).as_posix()
        trash = self.trash(operation.lesson_id).relative_to(self.workspace).as_posix()
        if operation.operation == ContentOperationKind.DELETE:
            expected = (lesson, trash)
        elif operation.operation == ContentOperationKind.RESTORE:
            expected = (trash, lesson)
        elif operation.operation == ContentOperationKind.PURGE:
            expected = (trash, self.staging(operation.id).relative_to(self.workspace).as_posix())
        else:
            raise ContentPathError("Неизвестный тип файловой операции")
        if (operation.source_relative_path, operation.destination_relative_path) != expected:
            raise ContentPathError("Журнал операции содержит неуправляемый путь")
        self.validate_path(self.workspace / expected[0])
        self.validate_path(self.workspace / expected[1])

    def is_owned_import_staging(self, candidate: Path) -> bool:
        return (
            candidate.parent == self.workspace / ".import-staging"
            and bool(_IMPORT_STAGE.fullmatch(candidate.name))
            and candidate.is_dir()
        )
