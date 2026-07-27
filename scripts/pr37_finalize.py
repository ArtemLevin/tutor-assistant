from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"Marker not found in {path}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/tutor_assistant/normalization/checkpoints.py",
    "    def recover_interrupted(self) -> int:\n",
    '''    def mark_indeterminate(self, run_id: int, chunk_index: int, error: str) -> None:\n        now = self._now()\n        with self.repository.connect() as db:\n            db.execute(\n                """\n                UPDATE normalization_chunks\n                SET status='indeterminate', error=?, updated_at=?\n                WHERE run_id=? AND chunk_index=?\n                """,\n                (error[-2000:], now, run_id, chunk_index),\n            )\n\n    def recover_interrupted(self) -> int:\n''',
)

replace_once(
    "src/tutor_assistant/normalization/service.py",
    '''                except Exception as exc:\n                    if run is not None:\n                        self.checkpoints.fail(\n                            run.id or 0,\n                            chunk.index,\n                            f"{type(exc).__name__}: {exc}",\n                        )\n                    raise\n''',
    '''                except Exception as exc:\n                    if run is not None:\n                        error = f"{type(exc).__name__}: {exc}"\n                        if self.config.provider == "yandex_ai_studio":\n                            self.checkpoints.mark_indeterminate(\n                                run.id or 0,\n                                chunk.index,\n                                error,\n                            )\n                            logging.warning(\n                                "event=content_filter_chunk_indeterminate lesson_id=%s "\n                                "run_id=%s chunk_index=%d error_code=%s",\n                                lesson_id,\n                                run.id,\n                                chunk.index,\n                                type(exc).__name__,\n                            )\n                        else:\n                            self.checkpoints.fail(run.id or 0, chunk.index, error)\n                    raise\n''',
)

replace_once(
    "tests/test_normalization_resume.py",
    '''from tutor_assistant.normalization.errors import (\n    NormalizationResumeConfirmationRequired,\n    OllamaTimeoutError,\n)\n''',
    '''from tutor_assistant.normalization.errors import (\n    NormalizationResumeConfirmationRequired,\n    OllamaTimeoutError,\n    YandexAIStudioUnavailableError,\n)\n''',
)
replace_once(
    "tests/test_normalization_resume.py",
    "from tutor_assistant.normalization.models import NormalizationRunStatus, SourceSegment\n",
    '''from tutor_assistant.normalization.models import (\n    NormalizationChunkStatus,\n    NormalizationRunStatus,\n    SourceSegment,\n)\n''',
)

resume_tests = ROOT / "tests/test_normalization_resume.py"
with resume_tests.open("a", encoding="utf-8") as stream:
    stream.write(
        '''\n\ndef test_completed_chunks_finalize_without_provider_replay(tmp_path: Path, monkeypatch) -> None:\n    import tutor_assistant.normalization.service as service_module\n\n    provider = FakeNormalizationProvider(\n        default=lambda request: f"[П] {request.segments[0].text}"\n    )\n    service, _content, lesson = _setup(tmp_path, provider)\n    original_write = service_module.write_text_atomic\n    writes = 0\n\n    def fail_once(path, text):\n        nonlocal writes\n        writes += 1\n        if writes == 1:\n            raise OSError("synthetic finalization failure")\n        return original_write(path, text)\n\n    monkeypatch.setattr(service_module, "write_text_atomic", fail_once)\n    with pytest.raises(OSError, match="synthetic finalization failure"):\n        service.normalize_lesson(lesson.lesson_id, source_segments=_segments())\n\n    provider_requests = len(provider.requests)\n    result = service.normalize_lesson(lesson.lesson_id, source_segments=_segments())\n\n    assert len(provider.requests) == provider_requests\n    assert result.transcript.statistics.reused_chunks == 3\n    assert result.transcript.statistics.provider_requests == 0\n\n\ndef test_yandex_runtime_failure_requires_confirmation_before_retry(tmp_path: Path) -> None:\n    provider = FakeNormalizationProvider(\n        responses=[YandexAIStudioUnavailableError("synthetic transport failure")]\n    )\n    service, _content, lesson = _setup(\n        tmp_path,\n        provider,\n        provider_name="yandex_ai_studio",\n    )\n    segments = _segments(1)\n\n    with pytest.raises(YandexAIStudioUnavailableError):\n        service.normalize_lesson(lesson.lesson_id, source_segments=segments)\n\n    run = service.runs.latest(lesson.lesson_id)\n    assert run is not None\n    checkpoint = service.checkpoints.get(run.id or 0, 0)\n    assert checkpoint is not None\n    assert checkpoint.status == NormalizationChunkStatus.INDETERMINATE\n    request_count = len(provider.requests)\n\n    with pytest.raises(NormalizationResumeConfirmationRequired):\n        service.normalize_lesson(lesson.lesson_id, source_segments=segments)\n    assert len(provider.requests) == request_count\n\n    result = service.normalize_lesson(\n        lesson.lesson_id,\n        source_segments=segments,\n        retry_indeterminate=True,\n    )\n    assert len(provider.requests) == request_count + 1\n    assert result.transcript.statistics.provider_requests == 1\n'''
    )

(ROOT / "tests/test_normalization_progress_gui.py").write_text(
    '''from __future__ import annotations\n\nfrom PySide6.QtCore import QCoreApplication\n\nfrom tutor_assistant.normalization.errors import NormalizationResumeConfirmationRequired\nfrom tutor_assistant.normalization.models import NormalizationProgress\nfrom tutor_assistant.ui.normalization_worker import NormalizationWorker\n\n\ndef _application() -> QCoreApplication:\n    return QCoreApplication.instance() or QCoreApplication([])\n\n\ndef test_normalization_worker_forwards_progress_and_result() -> None:\n    _application()\n\n    class Service:\n        def normalize_lesson(self, **kwargs):\n            kwargs["progress"](\n                NormalizationProgress(\n                    run_id=1,\n                    current_chunk=1,\n                    total_chunks=3,\n                    completed_chunks=2,\n                    reused_chunks=1,\n                    provider_requests=1,\n                    state="completed",\n                )\n            )\n            return "result"\n\n    progress = []\n    results = []\n    worker = NormalizationWorker(Service(), lesson_id="lesson")\n    worker.progress.connect(progress.append)\n    worker.succeeded.connect(results.append)\n\n    worker.run()\n\n    assert progress[0].completed_chunks == 2\n    assert progress[0].reused_chunks == 1\n    assert results == ["result"]\n\n\ndef test_normalization_worker_surfaces_resume_confirmation() -> None:\n    _application()\n\n    class Service:\n        def normalize_lesson(self, **_kwargs):\n            raise NormalizationResumeConfirmationRequired(4, (1, 3))\n\n    confirmations = []\n    worker = NormalizationWorker(Service(), lesson_id="lesson")\n    worker.resume_confirmation_required.connect(confirmations.append)\n\n    worker.run()\n\n    assert confirmations[0].run_id == 4\n    assert confirmations[0].chunk_indices == (1, 3)\n''',
    encoding="utf-8",
)

readme = ROOT / "README.md"
readme_text = readme.read_text(encoding="utf-8")
readme_text = readme_text.replace(
    "Для неопределённого запроса Yandex AI Studio требуется явное подтверждение.",
    "Для неопределённого или оборвавшегося запроса Yandex AI Studio требуется явное подтверждение.",
    1,
)
readme.write_text(readme_text, encoding="utf-8")

final_workflow = '''name: Windows student content\n\non:\n  pull_request:\n  push:\n    branches: [main]\n  workflow_dispatch:\n\npermissions:\n  contents: read\n\njobs:\n  content:\n    runs-on: windows-latest\n    timeout-minutes: 45\n    strategy:\n      fail-fast: false\n      matrix:\n        python-version: ['3.11', '3.12', '3.13', '3.14']\n    env:\n      QT_QPA_PLATFORM: offscreen\n    steps:\n      - uses: actions/checkout@v4\n        with:\n          fetch-depth: 0\n      - uses: actions/setup-python@v5\n        with:\n          python-version: ${{ matrix.python-version }}\n      - name: Install uv\n        run: python -m pip install uv\n      - name: Check lock file\n        run: uv lock --check\n      - name: Install test and desktop dependencies\n        run: uv sync --extra desktop --group dev\n      - name: Lint\n        run: uv run ruff check .\n      - name: Compile Python sources\n        run: uv run python -m compileall -q src/tutor_assistant tests\n      - name: Filtering import smoke\n        run: uv run python -c "from tutor_assistant.normalization.prompts import system_prompt; from tutor_assistant.ui.normalization_provider import provider_label; assert 'импульс' in system_prompt('physics').casefold(); assert provider_label('ollama') == 'Локальная LLM (Ollama)'"\n      - name: Filtering and checkpoint contracts\n        run: >-\n          uv run pytest -q\n          tests/test_filtering_feature_contracts.py\n          tests/test_subject_aware_filtering.py\n          tests/test_subject_manifest_compatibility.py\n          tests/test_normalization_provider_gui.py\n          tests/test_normalization_checkpoint_migration.py\n          tests/test_normalization_checkpoint_store.py\n          tests/test_normalization_resume.py\n          tests/test_normalization_progress_gui.py\n      - name: Validate patch whitespace\n        if: github.event_name == 'pull_request'\n        shell: bash\n        run: git diff --check ${{ github.event.pull_request.base.sha }}...HEAD\n      - name: Full test suite\n        run: uv run pytest --junitxml=pytest-report.xml\n      - name: Upload test report\n        if: always()\n        uses: actions/upload-artifact@v4\n        with:\n          name: windows-py${{ matrix.python-version }}-pytest-report\n          path: pytest-report.xml\n          if-no-files-found: ignore\n'''
(ROOT / ".github/workflows/windows-content.yml").write_text(final_workflow, encoding="utf-8")

temporary_paths = [
    ".github/workflows/pr37-bootstrap.yml",
    "scripts/pr37_bootstrap.py.gz.b64",
    "scripts/pr37_bootstrap.part.00",
    "scripts/pr37_bootstrap.part.01",
    "scripts/pr37_bootstrap.part.02",
    "scripts/pr37_bootstrap.part.03",
    "scripts/pr37_bootstrap.part.04",
    "scripts/pr37_bootstrap.part.04a",
    "scripts/pr37_bootstrap.part.04b",
    "scripts/pr37_bootstrap.part.04c",
    "scripts/pr37_bootstrap.part.05",
    "scripts/pr37_bootstrap_runner.sh",
    "scripts/pr37_finalize.py",
]
for relative in temporary_paths:
    (ROOT / relative).unlink(missing_ok=True)

print("PR 37 finalization changes applied")
