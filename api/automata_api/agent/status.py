import os

from automata_api.config import (
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL,
    AgentConfigurationError,
    get_agent_config,
)


def agent_status() -> dict[str, str]:
    try:
        config = get_agent_config()
    except AgentConfigurationError as error:
        return {
            "status": "missing_config",
            "message": str(error),
            "base_url": os.environ.get("AUTOMATA_LLM_BASE_URL") or DEFAULT_LLM_BASE_URL,
            "model": os.environ.get("AUTOMATA_LLM_MODEL") or DEFAULT_LLM_MODEL,
        }

    return {
        "status": "ready",
        "message": "DeepSeek agent configured",
        "base_url": config.base_url,
        "model": config.model,
    }


def agent_ready_message() -> str:
    status = agent_status()
    if status["status"] == "ready":
        return f"DeepSeek agent ready ({status['model']})"

    return status["message"]

