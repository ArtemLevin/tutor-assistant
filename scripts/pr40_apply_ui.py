from __future__ import annotations

from pathlib import Path


def patch(path: str, old: str, new: str, *, count: int = 1) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    actual = text.count(old)
    if actual != count:
        raise RuntimeError(f"{path}: marker count {actual}, expected {count}")
    file.write_text(text.replace(old, new), encoding="utf-8")


def append_end(path: str, addition: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if addition.strip() in text:
        return
    file.write_text(text.rstrip() + "\n" + addition, encoding="utf-8")


patch("src/tutor_assistant/ui/app.py", "    QSplitter,\n", "    QSpinBox,\n    QSplitter,\n")
patch(
    "src/tutor_assistant/ui/app.py",
    '''        self.normalization_model.setMinimumWidth(145)
        normalization_controls.addWidget(self.normalization_model)
        self.normalize_button = set_button_kind(
''',
    '''        self.normalization_model.setMinimumWidth(145)
        normalization_controls.addWidget(self.normalization_model)
        retry_label = QLabel("Повторных запросов")
        retry_label.setObjectName("muted")
        normalization_controls.addWidget(retry_label)
        self.normalization_retry_requests = QSpinBox()
        self.normalization_retry_requests.setRange(0, 3)
        self.normalization_retry_requests.setValue(
            self.config.normalization.retry_requests
        )
        self.normalization_retry_requests.setToolTip(
            "Количество дополнительных запросов после отклонённого ответа модели"
        )
        self.normalization_retry_requests.valueChanged.connect(
            self._normalization_retry_requests_changed
        )
        normalization_controls.addWidget(self.normalization_retry_requests)
        self.normalize_button = set_button_kind(
''',
)
patch(
    "src/tutor_assistant/ui/app.py",
    '''        self.retry_normalization_button = set_button_kind(
            QPushButton("Продолжить"),
            "ghost",
        )
        self.retry_normalization_button.clicked.connect(self.normalize_current_transcript)
''',
    '''        self.retry_normalization_button = set_button_kind(
            QPushButton("Запустить заново"),
            "ghost",
        )
        self.retry_normalization_button.clicked.connect(
            lambda: self.normalize_current_transcript(force=True)
        )
''',
)
patch(
    "src/tutor_assistant/ui/app.py",
    "    def _sync_normalization_provider_ui(self) -> None:\n",
    '''    def _normalization_retry_requests_changed(self, value: int) -> None:
        updated = self.config.normalization.model_copy(
            update={"retry_requests": value}
        )
        self._replace_normalization_config(updated)
        self._set_status(f"Повторных запросов LLM при ошибке: {value}")

    def _sync_normalization_provider_ui(self) -> None:
''',
)
patch(
    "src/tutor_assistant/ui/app.py",
    '''                    NormalizationRunStatus.FAILED,
                    NormalizationRunStatus.CANCELLED,
''',
    '''                    NormalizationRunStatus.REVIEW_REQUIRED,
                    NormalizationRunStatus.FAILED,
                    NormalizationRunStatus.CANCELLED,
''',
)
patch(
    "src/tutor_assistant/ui/app.py",
    '''        if dialog.exec() != QDialog.Accepted:
            return
        edited_text = dialog.edited_text
''',
    '''        outcome = dialog.exec()
        if dialog.restart_requested:
            self.normalize_current_transcript(force=True)
            return
        if outcome != QDialog.Accepted:
            return
        edited_text = dialog.edited_text
''',
)
patch(
    "src/tutor_assistant/ui/app.py",
    '''        warnings = len(result.transcript.quality.warnings)
        self._set_status(
            (
                f"LLM-фильтрация готова · сохранено "
''',
    '''        warnings = len(result.transcript.quality.warnings)
        fallback_chunks = result.transcript.statistics.source_fallback_chunks
        self._set_status(
            (
                (
                    "LLM-фильтрация завершена с замечаниями · "
                    f"исходный текст использован в блоках: {fallback_chunks} · "
                    if fallback_chunks
                    else "LLM-фильтрация готова · "
                )
                + "сохранено "
''',
)

patch(
    "src/tutor_assistant/ui/normalization.py",
    "        self.source_text = render_target_text(source_segments)\n",
    "        self.source_text = render_target_text(source_segments)\n"
    "        self.restart_requested = False\n",
)
patch(
    "src/tutor_assistant/ui/normalization.py",
    '''            f"сохранено учебного текста: {ratio:.1f}% · результат требует ручного применения"
''',
    '''            f"сохранено учебного текста: {ratio:.1f}% · "
            f"fallback-блоков: {transcript.statistics.source_fallback_chunks} · "
            "результат требует ручного применения"
''',
)
patch(
    "src/tutor_assistant/ui/normalization.py",
    '''        apply_button.setToolTip("Перед применением можно отредактировать отфильтрованный текст")
        buttons.addButton("Закрыть без применения", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
''',
    '''        apply_button.setToolTip("Перед применением можно отредактировать отфильтрованный текст")
        restart_button = buttons.addButton(
            "Запустить фильтрацию заново",
            QDialogButtonBox.ResetRole,
        )
        restart_button.clicked.connect(self._request_restart)
        buttons.addButton("Закрыть без применения", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
''',
)
patch(
    "src/tutor_assistant/ui/normalization.py",
    "    @property\n    def edited_text(self) -> str:\n",
    '''    def _request_restart(self) -> None:
        self.restart_requested = True
        self.reject()

    @property
    def edited_text(self) -> str:
''',
)

patch(
    "tests/test_normalization_service.py",
    '''    service, _content, lesson, _source_path = _setup(tmp_path, provider)

    result = service.normalize_lesson(lesson.lesson_id)

    assert result.run is not None
    assert result.run.attempts == 2
    assert len(provider.requests) == 2
''',
    '''    service, _content, lesson, _source_path = _setup(
        tmp_path,
        provider,
        config=NormalizationConfig(retry_requests=1, retry_backoff_seconds=0),
    )

    result = service.normalize_lesson(lesson.lesson_id)

    assert result.run is not None
    assert result.run.attempts == 2
    assert len(provider.requests) == 2
''',
)
patch(
    "tests/test_normalization_service.py",
    '''def test_failed_second_response_preserves_source_and_marks_run_failed(tmp_path: Path) -> None:
    provider = FakeNormalizationProvider(
        responses=[
            '{"text":"wrong contract"}',
            '{"text":"still wrong"}',
        ]
    )
    service, _content, lesson, source_path = _setup(tmp_path, provider)
    source_before = source_path.read_bytes()

    with pytest.raises(InvalidPlainTextOutputError):
        service.normalize_lesson(lesson.lesson_id)

    run = service.runs.latest(lesson.lesson_id)
    assert run and run.status == NormalizationRunStatus.FAILED
    assert source_path.read_bytes() == source_before
''',
    '''def test_rejected_responses_use_source_fallback_and_finish_review(tmp_path: Path) -> None:
    provider = FakeNormalizationProvider(
        responses=[
            '{"text":"wrong contract"}',
            '{"text":"still wrong"}',
        ]
    )
    service, _content, lesson, source_path = _setup(
        tmp_path,
        provider,
        config=NormalizationConfig(retry_requests=1, retry_backoff_seconds=0),
    )
    source_before = source_path.read_bytes()

    result = service.normalize_lesson(lesson.lesson_id)

    assert result.run and result.run.status == NormalizationRunStatus.REVIEW_REQUIRED
    assert result.transcript.statistics.source_fallback_chunks == 1
    assert result.transcript.quality.requires_manual_attention is True
    assert any("source_fallback:" in item for item in result.transcript.quality.warnings)
    assert "x + 2 > 5" in result.transcript.educational_text
    assert source_path.read_bytes() == source_before
''',
)
append_end(
    "tests/test_normalization_service.py",
    '''


def test_retry_requests_default_range_and_legacy_mapping() -> None:
    assert NormalizationConfig().retry_requests == 0
    assert NormalizationConfig().max_attempts == 1
    assert NormalizationConfig(max_attempts=4).retry_requests == 3
    with pytest.raises(ValueError):
        NormalizationConfig(retry_requests=4)
''',
)

patch("pyproject.toml", 'version = "0.15.0"', 'version = "0.16.0"')
patch("src/tutor_assistant/__init__.py", '__version__ = "0.15.0"', '__version__ = "0.16.0"')
patch("README.md", "Текущая версия: **0.15.0**.", "Текущая версия: **0.16.0**.")
append_end(
    "README.md",
    '''

## Устойчивое завершение LLM-фильтрации

При ошибке проверки ответа модели настройка **«Повторных запросов»** задаёт от 0 до 3
дополнительных запросов; значение по умолчанию — 0. После исчерпания лимита сервис
подставляет исходные реплики проблемного блока, продолжает остальные блоки и отдаёт
полный результат на ручную проверку. В окне проверки можно применить черновик,
закрыть его без применения или запустить фильтрацию заново.
''',
)
