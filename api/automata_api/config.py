import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_LLM_BASE_URL = "https://api.deepseek.com"
DEFAULT_LLM_MODEL = "deepseek-v4-pro"
DEFAULT_SYSTEM_PROMPT = """You are Automata, a real LLM-backed coding agent inside a local desktop workspace.
Respond in the user's language. Be concise, practical, and engineering-focused.
Use the prior session messages as context. If the user asks for code changes, give concrete file-level guidance and do not claim that files were changed unless an external tool actually changed them."""

MAX_CONTEXT_MESSAGES = 24


@dataclass(frozen=True)
class ApiConfig:
    host: str
    port: int
    cors_origins: tuple[str, ...]


@dataclass(frozen=True)
class DatabaseConfig:
    path: Path


@dataclass(frozen=True)
class AgentConfig:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float
    temperature: float


class AgentConfigurationError(RuntimeError):
    pass


def api_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def workspace_dir() -> Path:
    return api_dir().parent


def load_local_env() -> None:
    env_files = (
        workspace_dir() / ".env",
        api_dir() / ".env",
    )

    for env_file in env_files:
        if not env_file.exists():
            continue

        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue

            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)


def get_api_config() -> ApiConfig:
    return ApiConfig(
        host=os.environ.get("AUTOMATA_API_HOST", "127.0.0.1"),
        port=read_int_env("AUTOMATA_API_PORT", 8765),
        cors_origins=(
            "http://localhost:1420",
            "http://127.0.0.1:1420",
            "tauri://localhost",
        ),
    )


def get_database_config() -> DatabaseConfig:
    configured_dir = os.environ.get("AUTOMATA_DATA_DIR")
    if configured_dir:
        return DatabaseConfig(path=Path(configured_dir) / "automata.db")

    return DatabaseConfig(path=api_dir() / ".data" / "automata.db")


def get_agent_config() -> AgentConfig:
    api_key = (
        os.environ.get("AUTOMATA_LLM_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or ""
    ).strip()
    if not api_key:
        raise AgentConfigurationError(
            "Missing AUTOMATA_LLM_API_KEY. Add it to api/.env or the process environment."
        )

    return AgentConfig(
        api_key=api_key,
        base_url=(
            os.environ.get("AUTOMATA_LLM_BASE_URL") or DEFAULT_LLM_BASE_URL
        ).strip(),
        model=(os.environ.get("AUTOMATA_LLM_MODEL") or DEFAULT_LLM_MODEL).strip(),
        timeout_seconds=read_float_env("AUTOMATA_LLM_TIMEOUT_SECONDS", 120.0),
        temperature=read_float_env("AUTOMATA_LLM_TEMPERATURE", 0.2),
    )


def get_system_prompt() -> str:
    return os.environ.get("AUTOMATA_AGENT_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT)


def read_float_env(name: str, default: float) -> float:
    raw_value = os.environ.get(name)
    if raw_value is None or not raw_value.strip():
        return default

    try:
        return float(raw_value)
    except ValueError as error:
        raise AgentConfigurationError(f"{name} must be a number.") from error


def read_int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None or not raw_value.strip():
        return default

    try:
        return int(raw_value)
    except ValueError as error:
        raise AgentConfigurationError(f"{name} must be an integer.") from error
