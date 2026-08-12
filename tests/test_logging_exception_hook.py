from __future__ import annotations

import logging
import sys
from io import StringIO

from tutor_assistant.logging_config import install_exception_hook


def test_exception_hook_prunes_closed_stream_handlers() -> None:
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    original_hook = sys.excepthook
    open_stream = StringIO()
    closed_stream = StringIO()
    closed_stream.close()
    open_handler = logging.StreamHandler(open_stream)
    closed_handler = logging.StreamHandler(closed_stream)

    try:
        root.handlers = [open_handler, closed_handler]
        root.setLevel(logging.DEBUG)
        install_exception_hook()

        error = RuntimeError("boom")
        sys.excepthook(RuntimeError, error, None)

        assert closed_handler not in root.handlers
        assert open_handler in root.handlers
        assert "Необработанное исключение" in open_stream.getvalue()
    finally:
        sys.excepthook = original_hook
        root.handlers = original_handlers
        root.setLevel(original_level)
