from __future__ import annotations

import faulthandler
import logging
import sys
import threading
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .crash import write_crash_marker
from .runtime import build_identity
from .security.redaction import RedactingFormatter, SensitiveDataFilter

LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s | "
    "session=%(application_session_id)s | %(message)s"
)
_configured_workspace: Path | None = None
_activity_provider: Callable[[], dict[str, bool]] | None = None
_native_fault_stream = None


class BuildIdentityFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        identity = build_identity()
        record.application_session_id = identity.application_session_id
        record.application_version = identity.application_version
        record.build_commit_sha = identity.commit_sha
        record.release_channel = identity.release_channel
        return True


def log_directory(workspace: Path) -> Path:
    return workspace.expanduser() / "logs"


def configure_logging(workspace: Path, verbose: bool = False) -> Path:
    global _configured_workspace

    _configured_workspace = workspace.expanduser().resolve()
    directory = log_directory(workspace)
    directory.mkdir(parents=True, exist_ok=True)
    log_file = directory / "application.log"
    console_level = logging.DEBUG if verbose else logging.WARNING
    formatter = RedactingFormatter(LOG_FORMAT)
    sensitive_filter = SensitiveDataFilter()
    identity_filter = BuildIdentityFilter()
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(identity_filter)
    file_handler.addFilter(sensitive_filter)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(identity_filter)
    console_handler.addFilter(sensitive_filter)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(console_handler)
    logging.captureWarnings(True)
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger(__name__).info(
        "Логирование настроено: %s | version=%s channel=%s commit=%s mode=%s",
        log_file,
        build_identity().application_version,
        build_identity().release_channel,
        build_identity().commit_sha,
        "frozen" if build_identity().frozen else "source",
    )
    return log_file


def _prune_closed_stream_handlers() -> None:
    """Remove handlers whose stream was already closed by a test/app teardown."""
    root = logging.getLogger()
    for handler in tuple(root.handlers):
        stream = getattr(handler, "stream", None)
        if stream is not None and bool(getattr(stream, "closed", False)):
            root.removeHandler(handler)


def _record_crash(exc_type: type[BaseException], component: str) -> None:
    if _configured_workspace is None:
        return
    try:
        state = _activity_provider() if _activity_provider is not None else {}
        write_crash_marker(
            _configured_workspace,
            exception_type=exc_type,
            component=component,
            recording_active=state.get("recording_active", False),
            transcription_active=state.get("transcription_active", False),
        )
    except Exception:
        logging.getLogger("tutor_assistant.crash").error(
            "Не удалось безопасно сохранить маркер аварийного завершения"
        )


def install_exception_hook(
    workspace: Path | None = None,
    *,
    activity_provider: Callable[[], dict[str, bool]] | None = None,
) -> None:
    global _configured_workspace, _activity_provider

    if workspace is not None:
        _configured_workspace = workspace.expanduser().resolve()
    _activity_provider = activity_provider

    def handle_exception(exc_type, exc_value, traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, traceback)
            return
        # Pytest capture streams and GUI-owned streams can already be closed when
        # an exception escapes during teardown. Logging to such a handler emits a
        # secondary "I/O operation on closed file" error and obscures the real crash.
        _prune_closed_stream_handlers()
        logging.getLogger("tutor_assistant.crash").critical(
            "Необработанное исключение", exc_info=(exc_type, exc_value, traceback)
        )
        _record_crash(exc_type, "main-thread")

    def handle_thread_exception(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        _prune_closed_stream_handlers()
        logging.getLogger("tutor_assistant.crash").critical(
            "Необработанное исключение фонового потока",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        _record_crash(args.exc_type, "background-thread")

    sys.excepthook = handle_exception
    threading.excepthook = handle_thread_exception


def enable_native_fault_handler(workspace: Path) -> Path | None:
    global _native_fault_stream

    path = log_directory(workspace) / "native-crash.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open("a", encoding="utf-8")
        faulthandler.enable(file=stream, all_threads=True)
        if _native_fault_stream is not None and _native_fault_stream is not stream:
            _native_fault_stream.close()
        _native_fault_stream = stream
        return path
    except (OSError, RuntimeError, ValueError):
        logging.getLogger(__name__).warning("Native fault handler is unavailable on this platform")
        return None


def install_qt_message_handler() -> None:
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    except ImportError:
        return

    def handle_qt_message(message_type, _context, message: str) -> None:
        logger = logging.getLogger("tutor_assistant.qt")
        levels = {
            QtMsgType.QtDebugMsg: logging.DEBUG,
            QtMsgType.QtInfoMsg: logging.INFO,
            QtMsgType.QtWarningMsg: logging.WARNING,
            QtMsgType.QtCriticalMsg: logging.ERROR,
            QtMsgType.QtFatalMsg: logging.CRITICAL,
        }
        logger.log(levels.get(message_type, logging.INFO), "%s", message)
        if message_type == QtMsgType.QtFatalMsg:
            _record_crash(RuntimeError, "qt-fatal")

    qInstallMessageHandler(handle_qt_message)
