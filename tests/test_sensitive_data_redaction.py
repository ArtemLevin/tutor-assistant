from __future__ import annotations

import logging

from tutor_assistant.security.redaction import (
    RedactingFormatter,
    SensitiveDataFilter,
    find_secret_matches,
    redact_text,
)


def test_redacts_authorization_environment_json_and_url_credentials() -> None:
    source = (
        "Authorization: Api-Key abcdef123456 "
        "YANDEX_AI_STUDIO_API_KEY=qwerty-secret "
        '{"access_token":"token-value"} '
        "https://user:password@example.test"
    )

    redacted = redact_text(source)

    assert redacted.count("[REDACTED]") >= 4
    assert "abcdef123456" not in redacted
    assert "qwerty-secret" not in redacted
    assert "token-value" not in redacted
    assert "password" not in redacted
    assert not find_secret_matches(redacted)


def test_logging_filter_and_formatter_redact_exception_text() -> None:
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        "Authorization: Bearer %s",
        ("secret-token-value",),
        None,
    )
    sensitive = SensitiveDataFilter()
    assert sensitive.filter(record)
    rendered = RedactingFormatter("%(message)s").format(record)
    assert "secret-token-value" not in rendered
    assert "[REDACTED]" in rendered
