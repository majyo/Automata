from __future__ import annotations

import hashlib
import re
from typing import Any

SECRET_KEY_MARKERS = (
    "authorization",
    "api_key",
    "api-token",
    "api_token",
    "access_token",
    "cookie",
    "password",
    "secret",
)
BEARER_PATTERN = re.compile(
    r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"
)
KEY_VALUE_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|password|secret)"
    r"\s*[:=]\s*[^\s,;]+"
)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def redact_text(value: str, *, max_chars: int = 2_000) -> str:
    redacted = BEARER_PATTERN.sub(r"\1 [redacted]", value)
    redacted = KEY_VALUE_PATTERN.sub(r"\1=[redacted]", redacted)
    if len(redacted) > max_chars:
        return f"{redacted[:max_chars]}…[truncated]"
    return redacted


def redact_value(key: str, value: Any, *, content_mode: bool = False) -> Any:
    lowered = key.lower()
    if any(marker in lowered for marker in SECRET_KEY_MARKERS):
        return "[redacted]"
    if isinstance(value, dict):
        return {
            str(child_key): redact_value(
                str(child_key),
                child_value,
                content_mode=content_mode,
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [
            redact_value(key, item, content_mode=content_mode)
            for item in value
        ]
    if isinstance(value, tuple):
        return [
            redact_value(key, item, content_mode=content_mode)
            for item in value
        ]
    if isinstance(value, str):
        return redact_text(
            value,
            max_chars=200_000 if content_mode else 2_000,
        )
    if value is None or isinstance(value, bool | int | float):
        return value
    return redact_text(repr(value))


def redact_record(
    record: dict[str, Any], *, content_mode: bool = False
) -> dict[str, Any]:
    return {
        str(key): redact_value(
            str(key),
            value,
            content_mode=content_mode,
        )
        for key, value in record.items()
    }
