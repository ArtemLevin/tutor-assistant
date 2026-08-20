"""Synthetic disaster-recovery drills that never mutate the live workspace."""

from __future__ import annotations

import hashlib
import json
import tempfile
import wave
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from . import __version__
from .atomic_io import atomic_write_text
from .content import DatabaseBackupError, StudentContentService
from .domain import Lesson, Student
from .recording import recover_wav_recording


@dataclass(frozen=True, slots=True)
class RecoveryDrillCheck:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class RecoveryDrillReport:
    created_at: str
    application_version: str
    passed: bool
    live_workspace_unchanged: bool
    checks: tuple[RecoveryDrillCheck, ...]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["checks"] = [asdict(check) for check in self.checks]
        return payload


def _fingerprint(workspace: Path) -> dict[str, tuple[int, int, str]]:
    if not workspace.exists():
        return {}
    result: dict[str, tuple[int, int, str]] = {}
    for candidate in sorted(workspace.rglob("*")):
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(workspace).as_posix()
        stat = candidate.stat()
        digest = ""
        if candidate.suffix.lower() in {".sqlite", ".sqlite3", ".db", ".json", ".yaml", ".yml"}:
            checksum = hashlib.sha256()
            with candidate.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    checksum.update(chunk)
            digest = checksum.hexdigest()
        result[relative] = (stat.st_size, stat.st_mtime_ns, digest)
    return result


def _lesson(identifier: str) -> Lesson:
    return Lesson(
        lesson_id=identifier,
        student=Student(id="recovery-drill", full_name="Synthetic recovery drill"),
        subject="mathematics",
        topic="Synthetic recovery evidence",
        lesson_date=date(2026, 1, 1),
    )


def _recording(directory: Path, *, microphone: bool, system: bool, malformed: bool = False) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    session = directory / "session.json"
    atomic_write_text(
        session,
        (
            "{malformed"
            if malformed
            else json.dumps({"sample_rate": 8_000, "channels": 1, "status": "recording"})
        ),
    )
    for enabled, source in ((microphone, "microphone"), (system, "system")):
        if not enabled:
            continue
        chunks = directory / "chunks" / source
        chunks.mkdir(parents=True, exist_ok=True)
        with wave.open(str(chunks / "chunk_00000.wav"), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8_000)
            output.writeframes(b"\x00\x08" * 800)
    return directory


def _check_recording(directory: Path, name: str, *, microphone: bool, system: bool) -> RecoveryDrillCheck:
    try:
        result = recover_wav_recording(_recording(directory, microphone=microphone, system=system))
        return RecoveryDrillCheck(name, result.mixed_file.is_file(), "canonical WAV recovered")
    except Exception as exc:
        return RecoveryDrillCheck(name, False, type(exc).__name__)


def _database_checks(sandbox: Path) -> list[RecoveryDrillCheck]:
    checks: list[RecoveryDrillCheck] = []
    service = StudentContentService(sandbox)
    service.create_lesson(_lesson("before-backup"))
    backup = service.create_database_backup(reason="manual")
    checks.append(RecoveryDrillCheck("database_backup", backup.path.is_file()))
    verification = service.verify_database_backup(backup.path)
    checks.append(
        RecoveryDrillCheck("backup_verification", verification.valid, "; ".join(verification.errors))
    )
    service.create_lesson(_lesson("after-backup"))
    result = service.restore_database_backup(backup.path)
    checks.append(RecoveryDrillCheck("database_restore", result.verified))
    checks.append(
        RecoveryDrillCheck(
            "filesystem_projection",
            (sandbox / "lessons" / "before-backup" / "lesson.json").is_file(),
        )
    )
    manifests = list((sandbox / ".restore-quarantine").glob("*/manifest.json"))
    quarantine_ok = bool(manifests)
    if manifests:
        payload = json.loads(manifests[0].read_text(encoding="utf-8"))
        quarantine_ok = any(item.get("lesson_id") == "after-backup" for item in payload["lessons"])
        quarantine_ok = (
            quarantine_ok and (manifests[0].parent / "lessons" / "after-backup" / "lesson.json").is_file()
        )
    checks.append(RecoveryDrillCheck("post_backup_quarantine", quarantine_ok))

    current = service.get_lesson("before-backup")
    changed = current.lesson.model_copy(deep=True)
    changed.topic = "Rollback safety state"
    service.update_lesson(changed, expected_row_version=current.row_version)
    original_projection = service._synchronize_lesson_files
    calls = 0

    def fail_once(lesson_id: str, *, project_assets: bool = True) -> int:
        nonlocal calls
        calls += 1
        outcome = original_projection(lesson_id, project_assets=project_assets)
        if calls == 1:
            raise RuntimeError("synthetic projection failure")
        return outcome

    service._synchronize_lesson_files = fail_once  # type: ignore[method-assign]
    rollback_passed = False
    try:
        service.restore_database_backup(backup.path)
    except RuntimeError:
        rollback_passed = service.get_lesson("before-backup").lesson.topic == "Rollback safety state"
    finally:
        service._synchronize_lesson_files = original_projection  # type: ignore[method-assign]
    checks.append(RecoveryDrillCheck("rollback_to_safety_database", rollback_passed))

    malformed = service.create_database_backup(reason="manual")
    malformed.manifest_path.write_text("{not-json", encoding="utf-8")
    checks.append(
        RecoveryDrillCheck(
            "malformed_manifest_rejected",
            not service.verify_database_backup(malformed.path).valid,
        )
    )
    corrupted = service.create_database_backup(reason="manual")
    with corrupted.path.open("r+b") as stream:
        stream.seek(64)
        stream.write(b"corruption")
    rejected = not service.verify_database_backup(corrupted.path).valid
    try:
        service.restore_database_backup(corrupted.path)
        rejected = False
    except DatabaseBackupError:
        pass
    checks.append(RecoveryDrillCheck("sha_mismatch_and_corrupt_sqlite_rejected", rejected))
    collision_root = sandbox / ".synthetic-collision"
    original = collision_root / "original"
    quarantined = collision_root / "quarantined"
    original.mkdir(parents=True)
    quarantined.mkdir(parents=True)
    try:
        service._restore_quarantined_lesson_directories([(original, quarantined)])
        collision_rejected = False
    except DatabaseBackupError:
        collision_rejected = original.is_dir() and quarantined.is_dir()
    checks.append(RecoveryDrillCheck("quarantine_restore_collision_rejected", collision_rejected))
    report = service.inspect_content_integrity()
    checks.append(RecoveryDrillCheck("content_integrity", report.healthy))
    return checks


def _offline_restore_check(sandbox: Path) -> RecoveryDrillCheck:
    try:
        service = StudentContentService(sandbox)
        service.create_lesson(_lesson("offline-recovery"))
        backup = service.create_database_backup(reason="manual")
        database = sandbox / "tutor-assistant.sqlite3"
        Path(str(database) + "-wal").unlink(missing_ok=True)
        Path(str(database) + "-shm").unlink(missing_ok=True)
        database.write_bytes(b"synthetic corrupted SQLite")
        restored = StudentContentService.restore_database_backup_offline(sandbox, backup.path)
        recovered = StudentContentService(sandbox)
        passed = (
            restored.verified
            and restored.raw_safety_path is not None
            and recovered.get_lesson("offline-recovery").lesson.lesson_id == "offline-recovery"
        )
        return RecoveryDrillCheck("offline_corrupted_database_restore", passed)
    except Exception as exc:
        return RecoveryDrillCheck("offline_corrupted_database_restore", False, type(exc).__name__)


def run_recovery_drill(workspace: Path) -> RecoveryDrillReport:
    live = workspace.expanduser().resolve()
    before = _fingerprint(live)
    checks: list[RecoveryDrillCheck] = []
    try:
        with tempfile.TemporaryDirectory(prefix="tutor-assistant-recovery-drill-") as temporary:
            root = Path(temporary)
            checks.extend(_database_checks(root / "workspace"))
            checks.append(_offline_restore_check(root / "offline-workspace"))
            recordings = root / "recordings"
            checks.extend(
                (
                    _check_recording(
                        recordings / "dual",
                        "dual_channel_recovery",
                        microphone=True,
                        system=True,
                    ),
                    _check_recording(
                        recordings / "microphone-only",
                        "microphone_only_recovery",
                        microphone=True,
                        system=False,
                    ),
                    _check_recording(
                        recordings / "system-only",
                        "system_only_recovery",
                        microphone=False,
                        system=True,
                    ),
                )
            )
            neither = _recording(recordings / "neither", microphone=False, system=False)
            try:
                recover_wav_recording(neither)
                unrecoverable = False
            except RuntimeError:
                unrecoverable = True
            checks.append(RecoveryDrillCheck("missing_audio_explicitly_rejected", unrecoverable))
            damaged = _recording(
                recordings / "malformed-session",
                microphone=True,
                system=False,
                malformed=True,
            )
            checks.append(
                RecoveryDrillCheck(
                    "malformed_recording_metadata_recovered",
                    recover_wav_recording(damaged).mixed_file.is_file(),
                )
            )
    except Exception as exc:
        checks.append(RecoveryDrillCheck("drill_execution", False, type(exc).__name__))
    unchanged = before == _fingerprint(live)
    checks.append(RecoveryDrillCheck("live_workspace_unchanged", unchanged))
    return RecoveryDrillReport(
        created_at=datetime.now(UTC).isoformat(),
        application_version=__version__,
        passed=all(check.passed for check in checks),
        live_workspace_unchanged=unchanged,
        checks=tuple(checks),
    )


def write_recovery_drill_report(report: RecoveryDrillReport, output: Path, *, live_workspace: Path) -> Path:
    target = output.expanduser().resolve()
    live = live_workspace.expanduser().resolve()
    if target == live or live in target.parents:
        raise ValueError("Recovery evidence must be stored outside the live workspace")
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return target
