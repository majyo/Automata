import json
import os

import httpx
from fastapi import WebSocket

from automata_api.config import (
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL,
    MAX_CONTEXT_MESSAGES,
    AgentConfigurationError,
    get_agent_config,
    get_system_prompt,
    workspace_dir,
)
from automata_api.repositories.sessions import get_recent_messages, save_message
from automata_api.schemas import ChatPayload
from automata_api.services.llm import AgentProviderError, stream_llm_response


async def receive_payload(websocket: WebSocket) -> ChatPayload:
    message = await websocket.receive_text()
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        return {"type": "invalid"}

    if not isinstance(payload, dict):
        return {"type": "invalid"}

    return payload


async def stream_agent_reply(
    websocket: WebSocket, session_id: str, prompt: str
) -> None:
    await websocket.send_json(
        {"type": "started", "session_id": session_id, "prompt": prompt}
    )
    response = ""

    try:
        messages = fetch_agent_context(session_id)
        async for chunk in stream_llm_response(messages):
            response = f"{response}{chunk}"
            await websocket.send_json({"type": "token", "content": chunk})
    except AgentConfigurationError as error:
        await websocket.send_json({"type": "error", "message": str(error)})
        return
    except AgentProviderError as error:
        await websocket.send_json({"type": "error", "message": str(error)})
        return
    except httpx.RequestError as error:
        await websocket.send_json(
            {
                "type": "error",
                "message": f"LLM request failed: {error.__class__.__name__}",
            }
        )
        return

    if not response.strip():
        await websocket.send_json(
            {"type": "error", "message": "LLM provider returned an empty response."}
        )
        return

    message = save_message(session_id=session_id, role="agent", content=response)
    await websocket.send_json({"type": "done", "message": message})


def fetch_agent_context(session_id: str) -> list[dict[str, str]]:
    messages = [
        {
            "role": "system",
            "content": f"{get_system_prompt()}\n\nCurrent workspace: {agent_workspace()}",
        }
    ]

    for row in get_recent_messages(session_id, MAX_CONTEXT_MESSAGES):
        role = "assistant" if row["role"] == "agent" else "user"
        messages.append({"role": role, "content": row["content"]})

    return messages


def agent_workspace() -> str:
    return os.environ.get("AUTOMATA_WORKSPACE_DIR") or str(workspace_dir())


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
