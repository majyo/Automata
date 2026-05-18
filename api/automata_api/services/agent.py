import json
import os
from typing import Any

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
from automata_api.services.llm import AgentProviderError, create_llm_response
from automata_api.services.tools import (
    PlaceholderToolResult,
    placeholder_tool_specs,
    run_placeholder_tool,
)


MAX_AGENT_STEPS = 6
TOKEN_CHUNK_SIZE = 80


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
        response = await run_agent_loop(websocket, session_id)
        for chunk in chunk_text(response):
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

    message = save_message(session_id=session_id, role="agent", content=response)
    await websocket.send_json({"type": "done", "message": message})


async def run_agent_loop(websocket: WebSocket, session_id: str) -> str:
    config = get_agent_config()
    messages = fetch_agent_context(session_id)
    tools = placeholder_tool_specs()

    for step in range(1, MAX_AGENT_STEPS + 1):
        await websocket.send_json(
            {
                "type": "agent_step",
                "step": step,
                "message": f"Calling model {config.model}",
            }
        )
        assistant_message = await create_llm_response(messages, tools=tools)
        tool_calls = assistant_message.get("tool_calls")

        if isinstance(tool_calls, list) and tool_calls:
            messages.append(assistant_message_for_provider(assistant_message))
            for tool_call in tool_calls:
                await execute_tool_call(websocket, messages, tool_call)
            continue

        content = assistant_message.get("content")
        if isinstance(content, str) and content.strip():
            return content

        raise AgentProviderError("LLM provider returned an empty response.")

    raise AgentProviderError(
        f"Agent reached the maximum step limit ({MAX_AGENT_STEPS}) before finishing."
    )


async def execute_tool_call(
    websocket: WebSocket,
    messages: list[dict[str, Any]],
    tool_call: dict[str, Any],
) -> None:
    function = tool_call.get("function")
    if not isinstance(function, dict):
        raise AgentProviderError("LLM provider returned an invalid tool call.")

    name = function.get("name")
    arguments = function.get("arguments")
    if not isinstance(name, str) or not name.strip():
        raise AgentProviderError("LLM provider returned a tool call without a name.")

    await websocket.send_json(
        {
            "type": "tool_call",
            "tool": name,
            "arguments": arguments if isinstance(arguments, str) else "{}",
        }
    )
    result = run_placeholder_tool(name, arguments, agent_workspace())
    await websocket.send_json(
        {
            "type": "tool_result",
            "tool": result.name,
            "success": result.success,
            "content": result.content,
        }
    )
    messages.append(tool_result_for_provider(tool_call, result))


def fetch_agent_context(session_id: str) -> list[dict[str, Any]]:
    messages = [
        {
            "role": "system",
            "content": agent_system_prompt(),
        }
    ]

    for row in get_recent_messages(session_id, MAX_CONTEXT_MESSAGES):
        role = "assistant" if row["role"] == "agent" else "user"
        messages.append({"role": role, "content": row["content"]})

    return messages


def agent_system_prompt() -> str:
    return (
        f"{get_system_prompt()}\n\n"
        f"Current workspace: {agent_workspace()}\n\n"
        "You can use placeholder tools to simulate agent actions. Tool results "
        "are observations with simulated=true; they do not prove that files were "
        "read, commands were run, or edits were applied. Use the tool results to "
        "plan and explain, and be explicit when an observation is simulated."
    )


def assistant_message_for_provider(message: dict[str, Any]) -> dict[str, Any]:
    content = message.get("content")
    provider_message: dict[str, Any] = {
        "role": "assistant",
        "content": content if isinstance(content, str) and content else None,
    }

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        provider_message["tool_calls"] = tool_calls

    return provider_message


def tool_result_for_provider(
    tool_call: dict[str, Any], result: PlaceholderToolResult
) -> dict[str, Any]:
    call_id = tool_call.get("id")
    return {
        "role": "tool",
        "tool_call_id": call_id if isinstance(call_id, str) else "",
        "name": result.name,
        "content": result.content,
    }


def chunk_text(text: str) -> list[str]:
    return [
        text[index : index + TOKEN_CHUNK_SIZE]
        for index in range(0, len(text), TOKEN_CHUNK_SIZE)
    ]


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
