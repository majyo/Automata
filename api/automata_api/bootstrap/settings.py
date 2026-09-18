"""Configuration snapshot for one application instance.

Settings are read once, at the application boundary, and passed down as
plain frozen objects. Modules deeper in the stack must not read the
environment themselves; that is what makes the dependency graph testable
and keeps two app instances in one process from sharing configuration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from automata_api.config import (
    AgentConfig,
    AgentConfigurationError,
    ApiConfig,
    ContextCompressionConfig,
    DatabaseConfig,
    clear_local_env,
    get_agent_config,
    get_api_config,
    get_context_compression_config,
    get_database_config,
    get_system_prompt,
    load_local_env,
)

DEFAULT_RUN_EVENT_RETENTION_DAYS = 30
MAX_RUN_EVENT_RETENTION_DAYS = 3650
DEFAULT_RUN_EVENT_TOKEN_CHUNK_CHARS = 4096
DEFAULT_RUN_EVENT_MAX_BYTES = 65_536
DEFAULT_RUN_TOOL_OUTPUT_MAX_CHARS = 1_000_000


@dataclass(frozen=True)
class RunEventSettings:
    """Batching and bounding rules for the durable run event sink."""

    retention_days: int = DEFAULT_RUN_EVENT_RETENTION_DAYS
    token_chunk_chars: int = DEFAULT_RUN_EVENT_TOKEN_CHUNK_CHARS
    max_payload_bytes: int = DEFAULT_RUN_EVENT_MAX_BYTES
    max_tool_output_chars: int = DEFAULT_RUN_TOOL_OUTPUT_MAX_CHARS


@dataclass(frozen=True)
class AppSettings:
    """Everything one application instance needs to assemble itself.

    ``agent`` is optional because the API must still start (and report
    ``missing_config`` from ``/health``) when no model key is configured.
    """

    api: ApiConfig
    database: DatabaseConfig
    run_events: RunEventSettings
    system_prompt: str
    context_compression: ContextCompressionConfig
    agent: AgentConfig | None = None

    @property
    def data_directory(self) -> Path:
        return self.database.path.parent


def load_settings(*, load_env: bool = True) -> AppSettings:
    """Read the configuration snapshot for a new application instance.

    ``load_env`` mirrors the existing ``main.py`` behaviour of populating
    the process environment from ``.env`` candidates before reading; tests
    pass ``load_env=False`` so they keep full control of the environment.

    The environment is released again before returning. The snapshot is
    the configuration of record, and leaving ``.env`` values in
    ``os.environ`` would let them leak into later reads and into tests that
    deliberately delete a variable.
    """
    if load_env:
        load_local_env()

    try:
        return AppSettings(
            api=get_api_config(),
            database=get_database_config(),
            run_events=load_run_event_settings(),
            system_prompt=get_system_prompt(),
            context_compression=get_context_compression_config(),
            agent=load_agent_config(),
        )
    finally:
        if load_env:
            clear_local_env()


def load_agent_config() -> AgentConfig | None:
    """Return the model configuration, or ``None`` when it is missing.

    A missing model key is a degraded-but-startable state: ``/health``
    reports ``missing_config`` and a Run fails with a public error, which
    is the behaviour that existed before the container was introduced.
    """
    try:
        return get_agent_config()
    except AgentConfigurationError:
        return None


def load_run_event_settings() -> RunEventSettings:
    return RunEventSettings(
        retention_days=read_bounded_int(
            "AUTOMATA_RUN_EVENT_RETENTION_DAYS",
            DEFAULT_RUN_EVENT_RETENTION_DAYS,
            maximum=MAX_RUN_EVENT_RETENTION_DAYS,
        ),
        token_chunk_chars=read_positive_int(
            "AUTOMATA_RUN_EVENT_TOKEN_CHUNK_CHARS",
            DEFAULT_RUN_EVENT_TOKEN_CHUNK_CHARS,
        ),
        max_payload_bytes=read_positive_int(
            "AUTOMATA_RUN_EVENT_MAX_BYTES", DEFAULT_RUN_EVENT_MAX_BYTES
        ),
        max_tool_output_chars=read_positive_int(
            "AUTOMATA_RUN_TOOL_OUTPUT_MAX_CHARS",
            DEFAULT_RUN_TOOL_OUTPUT_MAX_CHARS,
        ),
    )


def read_positive_int(name: str, default: int) -> int:
    raw = _read_env(name)
    if raw is None:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def read_bounded_int(name: str, default: int, *, maximum: int) -> int:
    raw = _read_env(name)
    if raw is None:
        return default
    try:
        return max(0, min(int(raw), maximum))
    except ValueError:
        return default


def _read_env(name: str) -> str | None:
    raw = os.environ.get(name, "").strip()
    return raw or None
