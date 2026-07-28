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
        "Authorization: Api-Key placeholder-api-key-123456 "
        "YANDEX_AI_STUDIO_API_KEY=placeholder-yandex-key "
        '{"access_token":"placeholder-access-token"} '
        "https://user:placeholder-password@example.test"
    )

    redacted = redact_text(source)

    assert redacted.count("[REDACTED]") >= 4
    assert "placeholder-api-key-123456" not in redacted
    assert "placeholder-yandex-key" not in redacted
    assert "placeholder-access-token" not in redacted
    assert "placeholder-password" not in redacted
    assert not find_secret_matches(redacted)


def test_logging_filter_and_formatter_redact_exception_text() -> None:
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        __file__,
        1,
        "Authorization: Bearer %s",
        ("placeholder-secret-token-value",),
        None,
    )
    sensitive = SensitiveDataFilter()
    assert sensitive.filter(record)
    rendered = RedactingFormatter("%(message)s").format(record)
    assert "placeholder-secret-token-value" not in rendered
    assert "[REDACTED]" in rendered
