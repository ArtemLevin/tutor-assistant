from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ..config import AppConfig
from ..content import StudentContentService
from .credentials import credential_status
from .redaction import find_secret_matches, redact_text


class PrivacyCheck(BaseModel):
    name: str
    ok: bool
    required: bool = True
    detail: str


class PrivacyReport(BaseModel):
    ready: bool
    checks: list[PrivacyCheck] = Field(default_factory=list)

    @property
    def errors(self) -> list[str]:
        return [item.detail for item in self.checks if item.required and not item.ok]

    @property
    def warnings(self) -> list[str]:
        return [item.detail for item in self.checks if not item.required and not item.ok]


def run_privacy_diagnostics(
    config: AppConfig,
    config_path: Path = Path("config/app.yaml"),
) -> PrivacyReport:
    checks: list[PrivacyCheck] = []
    normalization = config.normalization

    checks.append(
        PrivacyCheck(
            name="cloud_policy",
            ok=normalization.effective_cloud_policy
            in {"disabled", "ask_every_time", "allow_for_session"},
            detail=f"Политика облака: {normalization.effective_cloud_policy}",
        )
    )
    checks.append(
        PrivacyCheck(
            name="official_yandex_endpoint",
            ok=normalization.yandex_base_url.rstrip("/")
            == "https://ai.api.cloud.yandex.net/v1",
            detail="Используется официальный HTTPS endpoint Yandex AI Studio",
        )
    )
    status = credential_status(normalization)
    checks.append(
        PrivacyCheck(
            name="credential_source",
            ok=status.configured or normalization.provider != "yandex_ai_studio",
            required=normalization.provider == "yandex_ai_studio",
            detail=status.detail,
        )
    )

    config_text = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    config_findings = find_secret_matches(config_text)
    checks.append(
        PrivacyCheck(
            name="config_has_no_secret",
            ok=not config_findings,
            detail="Секреты в YAML не обнаружены"
            if not config_findings
            else "В YAML обнаружено значение, похожее на секрет",
        )
    )

    gitignore = Path(".gitignore")
    ignored = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
    required_entries = {"config/app.yaml", "data/", ".env"}
    checks.append(
        PrivacyCheck(
            name="runtime_paths_ignored",
            ok=all(item in ignored for item in required_entries),
            detail="Runtime-конфиг, данные и .env исключены из Git",
        )
    )

    sample_one = "placeholder-privacy-doctor-key"
    sample_two = "placeholder-privacy-doctor-secret"
    redaction_sample = (
        f"Authorization: Api-Key {sample_one} "
        f"YANDEX_AI_STUDIO_API_KEY={sample_two}"
    )
    redacted = redact_text(redaction_sample)
    checks.append(
        PrivacyCheck(
            name="redaction_filter",
            ok="[REDACTED]" in redacted and not find_secret_matches(redacted),
            detail="Фильтр секретов маскирует Authorization и переменные окружения",
        )
    )

    try:
        service = StudentContentService(config.workspace)
        with service.repository.connect() as db:
            names = {
                str(row[0])
                for row in db.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name IN (
                        'cloud_processing_consents', 'cloud_request_events'
                    )
                    """
                ).fetchall()
            }
        tables_ready = names == {"cloud_processing_consents", "cloud_request_events"}
    except Exception as exc:
        tables_ready = False
        table_detail = f"Не удалось проверить migration 10: {type(exc).__name__}"
    else:
        table_detail = "Таблицы privacy-аудита доступны"
    checks.append(
        PrivacyCheck(
            name="privacy_migration",
            ok=tables_ready,
            detail=table_detail,
        )
    )

    ready = all(item.ok for item in checks if item.required)
    return PrivacyReport(ready=ready, checks=checks)


def format_privacy_report(report: PrivacyReport) -> str:
    lines = ["Privacy Doctor: " + ("ГОТОВ" if report.ready else "ТРЕБУЕТ НАСТРОЙКИ")]
    for item in report.checks:
        mark = "OK" if item.ok else ("ERROR" if item.required else "WARN")
        lines.append(f"[{mark}] {item.name}: {item.detail}")
    return "\n".join(lines)
