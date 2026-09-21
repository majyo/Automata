"""Argument decoding and coercion for tool calls.

These helpers are pure: they read a decoded argument mapping and either
return a coerced value or a diagnostic string. They perform no I/O and
know nothing about workspaces or processes.
"""

from __future__ import annotations

import json
from typing import Any

DEFAULT_BASH_TIMEOUT_SECONDS = 30.0
MAX_BASH_TIMEOUT_SECONDS = 120.0
MAX_PROCESS_YIELD_MILLISECONDS = 30_000
DEFAULT_STDIN_YIELD_MILLISECONDS = 250


def parse_tool_arguments(
    raw_arguments: str | dict[str, Any] | None,
) -> tuple[dict[str, Any], str | None]:
    """Decode model supplied tool arguments into a mapping."""
    if raw_arguments is None or raw_arguments == "":
        return {}, None

    if isinstance(raw_arguments, dict):
        return raw_arguments, None

    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError as error:
        return {}, f"Invalid JSON arguments: {error.msg}"

    if not isinstance(parsed, dict):
        return {}, "Tool arguments must be a JSON object."

    return parsed, None


def string_argument(arguments: dict[str, Any], name: str, default: str) -> str:
    value = arguments.get(name)
    if isinstance(value, str) and value.strip():
        return value
    return default


def bool_argument(arguments: dict[str, Any], name: str, default: bool) -> bool:
    value = arguments.get(name)
    return value if isinstance(value, bool) else default


def positive_int_argument(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


def timeout_argument(arguments: dict[str, Any]) -> float:
    raw_value = arguments.get("timeout_seconds", DEFAULT_BASH_TIMEOUT_SECONDS)
    if isinstance(raw_value, int | float):
        timeout_seconds = float(raw_value)
    elif isinstance(raw_value, str):
        try:
            timeout_seconds = float(raw_value)
        except ValueError:
            timeout_seconds = DEFAULT_BASH_TIMEOUT_SECONDS
    else:
        timeout_seconds = DEFAULT_BASH_TIMEOUT_SECONDS

    if timeout_seconds <= 0:
        return DEFAULT_BASH_TIMEOUT_SECONDS

    return min(timeout_seconds, MAX_BASH_TIMEOUT_SECONDS)


def max_output_chars_argument(arguments: dict[str, Any]) -> int:
    from automata_api.core.tools.constants import (
        DEFAULT_EXEC_OUTPUT_CHARS,
        MAX_EXEC_OUTPUT_CHARS,
    )

    raw_value = arguments.get("max_output_chars", DEFAULT_EXEC_OUTPUT_CHARS)
    if isinstance(raw_value, bool):
        return DEFAULT_EXEC_OUTPUT_CHARS
    if isinstance(raw_value, int):
        max_output_chars = raw_value
    elif isinstance(raw_value, float):
        max_output_chars = int(raw_value)
    elif isinstance(raw_value, str):
        try:
            max_output_chars = int(raw_value)
        except ValueError:
            return DEFAULT_EXEC_OUTPUT_CHARS
    else:
        return DEFAULT_EXEC_OUTPUT_CHARS

    if max_output_chars <= 0:
        return DEFAULT_EXEC_OUTPUT_CHARS

    return min(max_output_chars, MAX_EXEC_OUTPUT_CHARS)


def yield_time_ms_argument(
    arguments: dict[str, Any],
    *,
    default: int | None = None,
) -> int | None:
    if "yield_time_ms" not in arguments:
        return default
    raw_value = arguments.get("yield_time_ms")
    if isinstance(raw_value, bool):
        return default
    if isinstance(raw_value, int):
        yield_time_ms = raw_value
    elif isinstance(raw_value, float):
        yield_time_ms = int(raw_value)
    elif isinstance(raw_value, str):
        try:
            yield_time_ms = int(raw_value)
        except ValueError:
            return default
    else:
        return default
    return min(max(0, yield_time_ms), MAX_PROCESS_YIELD_MILLISECONDS)


def json_response(payload: dict[str, Any]) -> str:
    """Encode a tool payload the way every builtin tool reports results.

    ``ensure_ascii=True`` is deliberate: tool payloads are reproduced in
    tests and event logs, so escaping must be byte-stable.
    """
    return json.dumps(payload, ensure_ascii=True)
