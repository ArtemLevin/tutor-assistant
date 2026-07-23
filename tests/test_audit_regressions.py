from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
import yaml

import tutor_assistant.atomic_io as atomic_io
import tutor_assistant.publisher as publisher_module
import tutor_assistant.recording.recorder as recorder_module
from tutor_assistant.config import LatexConfig, RepositoryConfig, load_students
from tutor_assistant.content import ContentBusyError, StudentContentService
from tutor_assistant.content.coordination import ActivityLeaseStore
from tutor_assistant.crm import CrmStore
from tutor_assistant.domain import JobStatus, Lesson, Student
from tutor_assistant.latex.compiler import LatexCompiler
from tutor_assistant.latex.validator import validate_tex
from tutor_assistant.publisher import (
    GitError,
    LessonPublisher,
    ensure_private_repository,
    publication_payload_files,
)
from tutor_assistant.recording.recorder import (
    QueuedChunkWriter,
    find_recoverable_recordings,
    mix_tracks,
    recover_recording,
)


def make_lesson(identifier: str, topic: str = "Исходная тема") -> Lesson:
    return Lesson(
        lesson_id=identifier,
        student=Student(id="student", full_name="Тестовый ученик"),
        subject="mathematics",
        lesson_date=date(2026, 7, 23),
        topic=topic,
    )


def test_tracked_configuration_templates_are_safe_by_default() -> None:
    app = yaml.safe_load(Path("config/app.example.yaml").read_text(encoding="utf-8"))
    students = yaml.safe_load(Path("config/students.example.yaml").read_text(encoding="utf-8"))

    assert app["repository"]["push"] is False
    assert app["quick_start"]["last_student_id"] is None
    assert all(item["id"].startswith("example_") for item in students["students"])
    assert not Path("config/app.yaml").exists()
    assert not Path("config/students.yaml").exists()


def test_missing_local_students_file_is_an_explicit_empty_import(
    caplog,
    tmp_path: Path,
) -> None:
    assert load_students(tmp_path / "students.yaml") == []
    assert "CRM без YAML-импорта" in caplog.text


def test_publication_payload_preview_lists_only_existing_files(tmp_path: Path) -> None:
    lesson = make_lesson("payload-preview")
    transcript = tmp_path / "verified.txt"
    transcript.write_text("Transcript", encoding="utf-8")
    lesson.artifacts.verified_transcript = str(transcript)
    lesson.artifacts.cleaned_transcript = str(tmp_path / "missing.txt")

    assert publication_payload_files(lesson) == (
        "lesson.json",
        "job.status.json",
        "source/transcript.txt",
    )


@pytest.mark.parametrize(
    ("visibility", "allowed"),
    [("PRIVATE", True), ("PUBLIC", False), ("INTERNAL", False), ("", False)],
)
def test_publication_requires_verified_private_destination(
    monkeypatch,
    tmp_path: Path,
    visibility: str,
    allowed: bool,
) -> None:
    monkeypatch.setattr(publisher_module.shutil, "which", lambda _command: "/bin/gh")
    monkeypatch.setattr(
        publisher_module,
        "_run_command",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0],
            0,
            visibility,
            "",
        ),
    )
    config = RepositoryConfig(repository_full_name="owner/students")

    if allowed:
        ensure_private_repository(config, tmp_path)
    else:
        with pytest.raises(GitError, match="visibility"):
            ensure_private_repository(config, tmp_path)


def test_git_runner_is_noninteractive_and_bounded(monkeypatch, tmp_path: Path) -> None:
    observed: dict[str, object] = {}

    def timeout(command, **kwargs):
        observed.update(kwargs)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(publisher_module.subprocess, "run", timeout)

    with pytest.raises(GitError, match="timeout"):
        publisher_module.run_git(tmp_path, "fetch", timeout=0.01)

    environment = observed["env"]
    assert isinstance(environment, dict)
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GCM_INTERACTIVE"] == "Never"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("use_worktree", [True, False])
def test_publication_retry_resumes_app_owned_branch(
    monkeypatch,
    tmp_path: Path,
    use_worktree: bool,
) -> None:
    repository = tmp_path / "students"
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "init", "-b", "main", str(repository)],
        check=True,
        capture_output=True,
        text=True,
    )
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test")
    (repository / "README.md").write_text("test\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "Initial")
    _git(repository, "remote", "add", "origin", str(remote))
    _git(repository, "push", "-u", "origin", "main")

    config = RepositoryConfig(
        students_repo=repository,
        push=True,
        auto_create_pr=False,
        use_worktree=use_worktree,
        repository_full_name="owner/private-students",
    )
    lesson = make_lesson("retry-publication")
    lesson.status = JobStatus.READY
    real_run_git = publisher_module.run_git
    failed = False

    def fail_first_push(repo: Path, *args: str, **kwargs) -> str:
        nonlocal failed
        if args and args[0] == "push" and not failed:
            failed = True
            raise GitError("simulated transient failure")
        return real_run_git(repo, *args, **kwargs)

    monkeypatch.setattr(publisher_module, "ensure_private_repository", lambda *_args: None)
    monkeypatch.setattr(publisher_module, "run_git", fail_first_push)
    with pytest.raises(GitError, match="transient"):
        LessonPublisher(config).publish(lesson, tmp_path)

    monkeypatch.setattr(publisher_module, "run_git", real_run_git)
    result = LessonPublisher(config).publish(lesson, tmp_path)

    assert result.commit
    remote_branch = subprocess.run(
        ["git", "--git-dir", str(remote), "show-ref", "--verify", f"refs/heads/{result.branch}"],
        capture_output=True,
        text=True,
    )
    assert remote_branch.returncode == 0


def test_same_lesson_mutations_conflict_but_different_lessons_do_not(
    tmp_path: Path,
) -> None:
    store = ActivityLeaseStore(tmp_path / "operations.sqlite3")
    recording = store.try_acquire(
        owner_id="recorder",
        activity="recording",
        lesson_id="lesson-a",
    )
    same_lesson = store.try_acquire(
        owner_id="deleter",
        activity="content-delete",
        lesson_id="lesson-a",
    )
    other_lesson = store.try_acquire(
        owner_id="other",
        activity="transcription",
        lesson_id="lesson-b",
    )

    assert recording.acquired
    assert not same_lesson.acquired
    assert [item.activity for item in same_lesson.blockers] == ["recording"]
    assert other_lesson.acquired


def test_exclusive_restore_blocks_service_and_crm_writers(tmp_path: Path) -> None:
    workspace = tmp_path / "data"
    first = StudentContentService(workspace)
    second = StudentContentService(workspace)
    created = second.create_lesson(make_lesson("restore-guard"))
    content = second.get_lesson(created.lesson_id)
    crm = CrmStore(second.repository.path)

    with first.activity("database-restore", exclusive=True):
        with pytest.raises(ContentBusyError):
            second.update_lesson_metadata(
                created.lesson_id,
                student=content.lesson.student,
                subject=content.lesson.subject,
                lesson_date=content.lesson.lesson_date,
                topic="Concurrent edit",
                expected_updated_at=content.lesson.updated_at,
                expected_row_version=content.row_version,
            )
        with pytest.raises(ContentBusyError):
            second.save_transcript(created.lesson_id, "Concurrent transcript")
        with pytest.raises(ContentBusyError):
            crm.sync_students([Student(id="new", full_name="New student")])

    assert second.get_lesson(created.lesson_id).lesson.topic == "Исходная тема"


def test_restore_generation_rejects_stale_service_after_exclusive_release(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "data"
    restoring = StudentContentService(workspace)
    stale = StudentContentService(workspace)
    created = restoring.create_lesson(make_lesson("restore-generation"))
    backup = restoring.create_database_backup(reason="generation-test")
    snapshot = stale.get_lesson(created.lesson_id)

    restoring.restore_database_backup(backup.path)

    with pytest.raises(ContentBusyError, match="восстановлена"):
        stale.update_lesson_metadata(
            created.lesson_id,
            student=snapshot.lesson.student,
            subject=snapshot.lesson.subject,
            lesson_date=snapshot.lesson.lesson_date,
            topic="Stale edit",
            expected_updated_at=snapshot.lesson.updated_at,
            expected_row_version=snapshot.row_version,
        )


def test_existing_same_thread_lease_allows_nested_pipeline_write(
    tmp_path: Path,
) -> None:
    service = StudentContentService(tmp_path / "data")
    lesson = service.create_lesson(make_lesson("nested-write"))

    with service.activity("recording", lesson_id=lesson.lesson_id):
        lesson.transition(JobStatus.RECORDING)
        service.persist_pipeline_lesson(lesson, frozenset({"status", "error"}))

    assert service.get_lesson(lesson.lesson_id).lesson.status == JobStatus.RECORDING


def test_writer_stop_deadline_includes_full_queue_enqueue(tmp_path: Path) -> None:
    writer = QueuedChunkWriter.__new__(QueuedChunkWriter)
    writer.prefix = "hung"
    writer.queue = queue.Queue(maxsize=1)
    writer.queue.put(object())
    writer.error = None
    writer._stop_requested = SimpleNamespace(set=lambda: None)

    class HungThread:
        @staticmethod
        def is_alive() -> bool:
            return True

        @staticmethod
        def join(_timeout: float) -> None:
            return None

    writer.thread = HungThread()
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="не приняла stop"):
        writer.stop(0.05)
    assert time.monotonic() - started < 0.3


def test_writer_exits_cooperatively_after_stop_enqueue_times_out(
    monkeypatch,
    tmp_path: Path,
) -> None:
    entered_write = threading.Event()
    release_write = threading.Event()

    class BlockingFile:
        def write(self, _data) -> None:
            entered_write.set()
            release_write.wait(1)

        @staticmethod
        def flush() -> None:
            return None

        @staticmethod
        def close() -> None:
            return None

    monkeypatch.setattr(QueuedChunkWriter, "_open", lambda *_args: BlockingFile())
    writer = QueuedChunkWriter(
        tmp_path,
        "mic",
        8000,
        1,
        1,
        1,
        lambda: None,
        lambda _value: None,
    )
    writer.enqueue(np.ones((10, 1), dtype="float32"), time.monotonic())
    assert entered_write.wait(1)
    writer.enqueue(np.ones((10, 1), dtype="float32"), time.monotonic())

    with pytest.raises(RuntimeError, match="не приняла stop"):
        writer.stop(0.05)
    release_write.set()
    writer.thread.join(1)

    assert not writer.thread.is_alive()


def test_writer_finalization_error_reaches_stop(tmp_path: Path) -> None:
    writer = QueuedChunkWriter(
        tmp_path,
        "mic",
        8000,
        1,
        1,
        2,
        lambda: (_ for _ in ()).throw(PermissionError("manifest locked")),
        lambda _value: None,
    )

    with pytest.raises(RuntimeError, match="Ошибка writer-потока"):
        writer.stop()

    assert writer.error is not None
    assert "manifest locked" in str(writer.error)


def test_atomic_lock_exhaustion_preserves_old_target(
    monkeypatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "lesson.json"
    target.write_text('{"topic":"old"}', encoding="utf-8")
    monkeypatch.setattr(
        Path,
        "replace",
        lambda _source, destination: (_ for _ in ()).throw(PermissionError(5, "locked", str(destination))),
    )
    monkeypatch.setattr(atomic_io, "sleep", lambda _seconds: None)
    monkeypatch.setattr(atomic_io, "ATOMIC_WRITE_ATTEMPTS", 2)

    with pytest.raises(PermissionError, match="атомарно"):
        atomic_io.atomic_write_text(target, '{"topic":"new"}')

    assert json.loads(target.read_text(encoding="utf-8")) == {"topic": "old"}
    assert not list(tmp_path.glob(".lesson.json.*.tmp"))


def _recording_fixture(recording: Path, *, status: str | None) -> None:
    microphone = recording / "chunks" / "microphone"
    system = recording / "chunks" / "system"
    microphone.mkdir(parents=True)
    system.mkdir(parents=True)
    payload = np.full((1600, 1), 0.1, dtype="float32")
    sf.write(microphone / "mic_00000.wav", payload, 8000)
    sf.write(system / "system_00000.wav", payload, 8000)
    session = recording / "session.json"
    if status is None:
        session.write_text("{broken", encoding="utf-8")
    else:
        session.write_text(
            json.dumps({"sample_rate": 8000, "channels": 1, "status": status}),
            encoding="utf-8",
        )


@pytest.mark.parametrize("status", ["recording", "recorded", "failed_to_start", "failed_to_stop", None])
def test_recoverable_recording_status_matrix(
    tmp_path: Path,
    status: str | None,
) -> None:
    recording = tmp_path / "lessons" / f"lesson-{status}" / "recording"
    _recording_fixture(recording, status=status)

    assert recording in find_recoverable_recordings(tmp_path)


def test_corrupt_manifest_recovery_uses_bounded_streaming_fallback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    recording = tmp_path / "recording"
    _recording_fixture(recording, status=None)
    read_sizes: list[int] = []
    real_read = sf.SoundFile.read

    def observed_read(self, frames=-1, *args, **kwargs):
        if isinstance(frames, int):
            read_sizes.append(frames)
        return real_read(self, frames, *args, **kwargs)

    monkeypatch.setattr(recorder_module.shutil, "which", lambda _command: None)
    monkeypatch.setattr(sf.SoundFile, "read", observed_read)

    result = recover_recording(recording)

    assert result.mixed_file.is_file()
    assert read_sizes
    assert max(read_sizes) <= recorder_module._MIX_BLOCK_FRAMES + 1


def test_recovery_mixing_reads_large_tracks_in_bounded_blocks(
    monkeypatch,
    tmp_path: Path,
) -> None:
    microphone = tmp_path / "microphone.wav"
    system = tmp_path / "system.wav"
    output = tmp_path / "lesson.wav"
    payload = np.linspace(-0.25, 0.25, 200_000, dtype="float32").reshape(-1, 1)
    sf.write(microphone, payload, 8000)
    sf.write(system, payload, 8000)
    read_sizes: list[int] = []
    real_read = sf.SoundFile.read

    def observed_read(self, frames=-1, *args, **kwargs):
        if isinstance(frames, int):
            read_sizes.append(frames)
        return real_read(self, frames, *args, **kwargs)

    monkeypatch.setattr(recorder_module.shutil, "which", lambda _command: None)
    monkeypatch.setattr(sf.SoundFile, "read", observed_read)
    mix_tracks(microphone, system, output, 8000, 8000, 8000, 0, 0)

    assert sf.info(output).frames == len(payload)
    assert max(read_sizes) <= recorder_module._MIX_BLOCK_FRAMES + 1


def test_gui_recovery_starts_worker_instead_of_running_inline(
    monkeypatch,
    tmp_path: Path,
) -> None:
    pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    from PySide6.QtWidgets import QMessageBox

    from tutor_assistant.ui import app as app_module

    class Signal:
        def connect(self, callback) -> None:
            self.callback = callback

    class FakeWorker:
        def __init__(self, callable_, *args) -> None:
            self.callable = callable_
            self.args = args
            self.succeeded = Signal()
            self.failed = Signal()
            self.finished = Signal()
            self.started = False

        def start(self) -> None:
            self.started = True

    session = tmp_path / "lessons" / "lesson" / "recording"
    workers: list[FakeWorker] = []
    window = SimpleNamespace(
        config=SimpleNamespace(workspace=tmp_path),
        workers=workers,
        _recovery_sessions=[],
        _set_status=lambda *_args: None,
        _recovery_ready=lambda *_args: None,
        _recovery_failed=lambda *_args: None,
        _operation_failed=lambda *_args: None,
        _worker_finished=lambda *_args: None,
    )
    window._offer_next_recovery = lambda: app_module.MainWindow._offer_next_recovery(window)
    monkeypatch.setattr(app_module, "find_recoverable_recordings", lambda _path: [session])
    monkeypatch.setattr(app_module, "Worker", FakeWorker)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.Yes)

    app_module.MainWindow._offer_recovery(window)

    assert len(workers) == 1
    assert workers[0].started
    assert workers[0].callable is recover_recording


def test_yaml_sync_is_insert_only_for_editable_crm_name(tmp_path: Path) -> None:
    store = CrmStore(tmp_path / "assistant.sqlite3")
    source = Student(id="student", full_name="Imported name")
    store.sync_students([source])
    profile = store.get_student(source.id)
    assert profile is not None
    profile.full_name = "Edited in CRM"
    store.save_student(profile, [])

    store.sync_students([source.model_copy(update={"full_name": "Changed YAML"})])
    store.sync_students([Student(id="new-student", full_name="New import")])

    assert store.get_student(source.id).full_name == "Edited in CRM"
    assert store.get_student("new-student").full_name == "New import"


def test_latex_source_tree_rejects_file_and_directory_symlinks(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("SECRET", encoding="utf-8")
    tex = source / "lesson.tex"
    tex.write_text(
        r"\documentclass{article}\begin{document}\input{leak.txt}\end{document}",
        encoding="utf-8",
    )
    try:
        (source / "leak.txt").symlink_to(outside / "secret.txt")
        (source / "linked-directory").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")

    issues = validate_tex(tex)

    assert [issue.code for issue in issues].count("unsafe-link") == 2

    outside_tex = outside / "outside.tex"
    outside_tex.write_text(
        r"\documentclass{article}\begin{document}Outside\end{document}",
        encoding="utf-8",
    )
    linked_tex = tmp_path / "linked.tex"
    linked_tex.symlink_to(outside_tex)
    result = LatexCompiler(LatexConfig()).compile(linked_tex)
    assert not result.success
    assert any(issue.code == "unsafe-link" for issue in result.validation_issues)


def test_windows_ci_runs_full_suite_for_every_pull_request() -> None:
    workflow = Path(".github/workflows/windows-content.yml").read_text(encoding="utf-8")

    assert "pull_request:\n    paths:" not in workflow
    assert "Full test suite" in workflow
    assert "uv run pytest" in workflow
    assert "tests/test_content_coordination.py" not in workflow
