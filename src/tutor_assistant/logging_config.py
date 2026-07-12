from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


def log_directory(workspace: Path) -> Path:
    return workspace.expanduser() / "logs"


def configure_logging(workspace: Path, verbose: bool = False) -> Path:
    directory = log_directory(workspace)
    directory.mkdir(parents=True, exist_ok=True)
    log_file = directory / "application.log"
    console_level = logging.DEBUG if verbose else logging.WARNING
    formatter = logging.Formatter(LOG_FORMAT)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(console_handler)
    logging.captureWarnings(True)
    logging.getLogger(__name__).info("Логирование настроено: %s", log_file)
    return log_file


def install_exception_hook() -> None:
    def handle_exception(exc_type, exc_value, traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, traceback)
            return
        logging.getLogger("tutor_assistant.crash").critical(
            "Необработанное исключение", exc_info=(exc_type, exc_value, traceback)
        )

    sys.excepthook = handle_exception
