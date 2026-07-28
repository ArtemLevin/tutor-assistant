from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

REDACTED = "[REDACTED]"

_PATTERNS = (
    re.compile(r"(?i)(\bAuthorization\s*[:=]\s*(?:Api-Key|Bearer)\s+)([^\s,;]+)"),
    re.compile(r"(?i)(\b(?:Api-Key|Bearer)\s+)([A-Za-z0-9._~+/=-]{6,})"),
    re.compile(r"(?i)(\b(?:YANDEX_AI_STUDIO_API_KEY|GITHUB_TOKEN)\s*=\s*)([^\s,;]+)"),
    re.compile(
        r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password)"
        r"[\"']?\s*[:=]\s*[\"']?)([^\"'\s,}]{6,})"
    ),
    re.compile(r"(?i)(https?://)([^/@\s:]+):([^/@\s]+)@"),
)


def redact_text(value: str) -> str:
    redacted = value
    for index, pattern in enumerate(_PATTERNS):
        if index == len(_PATTERNS) - 1:
            redacted = pattern.sub(rf"\1{REDACTED}:{REDACTED}@", redacted)
        else:
            redacted = pattern.sub(rf"\1{REDACTED}", redacted)
    return redacted


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {key: redact_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return type(value)(redact_value(item) for item in value)
    return value


def find_secret_matches(value: str) -> list[str]:
    findings: list[str] = []
    for pattern in _PATTERNS:
        for match in pattern.finditer(value):
            candidate = match.group(0)
            if REDACTED not in candidate:
                findings.append(candidate[:120])
    return findings


class SensitiveDataFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Resolve lazy logging interpolation before redaction. Replacing only
        # record.msg while retaining the original args can leave stale `%s`
        # placeholders and make Formatter raise TypeError.
        record.msg = redact_text(record.getMessage())
        record.args = ()
        return True


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))
