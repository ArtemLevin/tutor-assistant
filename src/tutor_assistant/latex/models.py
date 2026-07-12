from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    line: int | None = None


@dataclass
class EnvironmentReport:
    ready: bool
    latexmk: str | None
    engine: str | None
    pdftoppm: str | None
    packages: dict[str, bool] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CompilationResult:
    success: bool
    tex_file: Path
    pdf_file: Path | None
    log_file: Path
    report_file: Path
    duration_seconds: float
    pages: int = 0
    size_bytes: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    validation_issues: list[ValidationIssue] = field(default_factory=list)
    preview_files: list[Path] = field(default_factory=list)
    timed_out: bool = False
    fix_request_file: Path | None = None

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in ("tex_file", "pdf_file", "log_file", "report_file", "fix_request_file"):
            data[key] = str(data[key]) if data[key] else None
        data["preview_files"] = [str(path) for path in self.preview_files]
        return data
