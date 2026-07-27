from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from automata_api.config import get_database_config

ObservabilityMode = Literal["diagnostic", "profile"]


class ObservabilityConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ObservabilityConfig:
    mode: ObservabilityMode
    capture_content: bool
    output_dir: Path
    queue_size: int
    critical_queue_size: int
    sample_interval_ms: int
    file_max_bytes: int
    log_retention_days: int
    log_max_bytes: int
    profile_retention_days: int
    profile_max_bytes: int
    content_profile_retention_hours: int
    content_profile_max_bytes: int
    log_level: str

    @property
    def profile_enabled(self) -> bool:
        return self.mode == "profile"


def get_observability_config() -> ObservabilityConfig:
    raw_mode = os.environ.get(
        "AUTOMATA_OBSERVABILITY_MODE", "diagnostic"
    ).strip().lower()
    if raw_mode not in {"diagnostic", "profile"}:
        raise ObservabilityConfigurationError(
            "AUTOMATA_OBSERVABILITY_MODE must be diagnostic or profile."
        )
    mode: ObservabilityMode = (
        "profile" if raw_mode == "profile" else "diagnostic"
    )
    capture_content = read_bool_env(
        "AUTOMATA_PROFILE_CAPTURE_CONTENT", False
    )
    if capture_content and mode != "profile":
        raise ObservabilityConfigurationError(
            "AUTOMATA_PROFILE_CAPTURE_CONTENT requires "
            "AUTOMATA_OBSERVABILITY_MODE=profile."
        )

    configured_dir = os.environ.get("AUTOMATA_OBSERVABILITY_DIR", "").strip()
    output_dir = (
        Path(configured_dir).expanduser()
        if configured_dir
        else get_database_config().path.parent / "observability"
    )
    return ObservabilityConfig(
        mode=mode,
        capture_content=capture_content,
        output_dir=output_dir.resolve(),
        queue_size=read_positive_int_env(
            "AUTOMATA_OBSERVABILITY_QUEUE_SIZE", 8192
        ),
        critical_queue_size=read_positive_int_env(
            "AUTOMATA_OBSERVABILITY_CRITICAL_QUEUE_SIZE", 256
        ),
        sample_interval_ms=read_bounded_int_env(
            "AUTOMATA_PROFILE_SAMPLE_INTERVAL_MS",
            200,
            minimum=50,
            maximum=60_000,
        ),
        file_max_bytes=read_positive_int_env(
            "AUTOMATA_OBSERVABILITY_FILE_MAX_BYTES", 16 * 1024 * 1024
        ),
        log_retention_days=read_non_negative_int_env(
            "AUTOMATA_LOG_RETENTION_DAYS", 30
        ),
        log_max_bytes=read_positive_int_env(
            "AUTOMATA_LOG_MAX_BYTES", 512 * 1024 * 1024
        ),
        profile_retention_days=read_non_negative_int_env(
            "AUTOMATA_PROFILE_RETENTION_DAYS", 7
        ),
        profile_max_bytes=read_positive_int_env(
            "AUTOMATA_PROFILE_MAX_BYTES", 2 * 1024 * 1024 * 1024
        ),
        content_profile_retention_hours=read_non_negative_int_env(
            "AUTOMATA_CONTENT_PROFILE_RETENTION_HOURS", 24
        ),
        content_profile_max_bytes=read_positive_int_env(
            "AUTOMATA_CONTENT_PROFILE_MAX_BYTES", 500 * 1024 * 1024
        ),
        log_level=(
            os.environ.get("AUTOMATA_OBSERVABILITY_LOG_LEVEL", "INFO")
            .strip()
            .upper()
            or "INFO"
        ),
    )


def read_bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ObservabilityConfigurationError(f"{name} must be a boolean.")


def read_positive_int_env(name: str, default: int) -> int:
    value = read_int_env(name, default)
    if value <= 0:
        raise ObservabilityConfigurationError(
            f"{name} must be greater than 0."
        )
    return value


def read_non_negative_int_env(name: str, default: int) -> int:
    value = read_int_env(name, default)
    if value < 0:
        raise ObservabilityConfigurationError(
            f"{name} must be non-negative."
        )
    return value


def read_bounded_int_env(
    name: str, default: int, *, minimum: int, maximum: int
) -> int:
    value = read_int_env(name, default)
    if value < minimum or value > maximum:
        raise ObservabilityConfigurationError(
            f"{name} must be between {minimum} and {maximum}."
        )
    return value


def read_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise ObservabilityConfigurationError(
            f"{name} must be an integer."
        ) from error
