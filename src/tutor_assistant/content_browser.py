from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from .content import LessonContent, LessonPage
from .domain import JobStatus

STATUS_LABELS: dict[JobStatus, str] = {
    JobStatus.DRAFT: "Черновик",
    JobStatus.RECORDING: "Идёт запись",
    JobStatus.RECORDED: "Записано",
    JobStatus.TRANSCRIBING: "Транскрибируется",
    JobStatus.REVIEW_REQUIRED: "Нужна проверка",
    JobStatus.READY: "Готово к публикации",
    JobStatus.PUBLISHED: "Опубликовано",
    JobStatus.GENERATED_TEX: "TEX создан",
    JobStatus.COMPILING_PDF: "PDF собирается",
    JobStatus.PDF_REVIEW_REQUIRED: "PDF ждёт проверки",
    JobStatus.COMPILE_FAILED: "Ошибка PDF",
    JobStatus.GENERATING: "Материалы создаются",
    JobStatus.COMPLETED: "Завершено",
    JobStatus.FAILED: "Ошибка",
}


@dataclass(frozen=True)
class ContentFileRow:
    kind: str
    display_path: str
    absolute_path: Path | None
    size_bytes: int
    state: str
    registered: bool

    @property
    def exists(self) -> bool:
        return self.state == "available"

    @property
    def state_label(self) -> str:
        if self.state == "available":
            return "Доступен"
        if self.state == "outside_workspace":
            return "Вне каталога данных"
        return "Файл отсутствует"


def status_label(status: JobStatus) -> str:
    return STATUS_LABELS.get(status, status.value)


def is_audio_path(path: Path) -> bool:
    return path.suffix.casefold() in {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}


def format_size(size_bytes: int) -> str:
    value = float(max(0, size_bytes))
    for suffix in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024 or suffix == "ГБ":
            return f"{value:.0f} {suffix}" if suffix == "Б" else f"{value:.1f} {suffix}"
        value /= 1024
    return f"{value:.1f} ГБ"


def pagination_text(page: LessonPage) -> str:
    if page.total == 0:
        return "Занятия не найдены"
    start = page.offset + 1
    end = min(page.offset + len(page.items), page.total)
    return f"{start}–{end} из {page.total}"


def _resolve_known_path(value: str, workspace: Path) -> tuple[Path | None, str, str]:
    raw = value.strip()
    if not raw:
        return None, "", "missing"
    path = Path(raw)
    windows = PureWindowsPath(raw)
    if windows.drive and not path.is_absolute():
        return None, raw, "outside_workspace"
    candidate = path if path.is_absolute() else workspace / path
    resolved = candidate.resolve()
    try:
        display = resolved.relative_to(workspace).as_posix()
    except ValueError:
        return None, raw, "outside_workspace"
    return resolved, display, "available" if resolved.is_file() else "missing"


def content_file_rows(content: LessonContent, workspace: Path) -> list[ContentFileRow]:
    workspace = workspace.resolve()
    rows: dict[str, ContentFileRow] = {}

    def add(
        value: str | None,
        kind: str,
        *,
        size_bytes: int = 0,
        registered: bool = False,
    ) -> None:
        if not value:
            return
        absolute, display, state = _resolve_known_path(value, workspace)
        if not display:
            return
        measured_size = size_bytes
        if absolute is not None and state == "available" and not measured_size:
            try:
                measured_size = absolute.stat().st_size
            except OSError:
                state = "missing"
        existing = rows.get(display)
        if existing and existing.registered:
            return
        rows[display] = ContentFileRow(
            kind=kind,
            display_path=display,
            absolute_path=absolute,
            size_bytes=measured_size,
            state=state,
            registered=registered,
        )

    for asset in content.assets:
        add(
            asset.relative_path,
            asset.kind.value,
            size_bytes=asset.size_bytes,
            registered=True,
        )
    if content.transcript:
        add(content.transcript.relative_path, "transcript", registered=True)

    lesson = content.lesson
    add(f"lessons/{lesson.lesson_id}/lesson.json", "metadata")
    add(lesson.source_audio_local, "audio")
    for name, value in lesson.artifacts.model_dump().items():
        add(value, "transcript" if "transcript" in name else "document")
    if lesson.latex.tex_path:
        add(lesson.latex.tex_path, "document")
    if lesson.latex.pdf_path:
        add(lesson.latex.pdf_path, "document")
    if lesson.latex.report_path:
        add(lesson.latex.report_path, "document")
    for preview in lesson.latex.preview_paths:
        add(preview, "document")

    return sorted(rows.values(), key=lambda item: (item.kind, item.display_path.casefold()))
