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
    ContextCompressionConfig,
    get_agent_config,
    get_context_compression_config,
    get_system_prompt,
    workspace_dir,
)
from automata_api.repositories.sessions import (
    fetch_context_summary,
    get_messages_after_sequence,
    get_recent_messages,
    save_message,
    upsert_context_summary,
)
from automata_api.schemas import ChatPayload
from automata_api.services.llm import AgentProviderError, create_llm_response
from automata_api.services.tools import (
    ToolResult,
    placeholder_tool_specs,
    run_tool,
)


MAX_AGENT_STEPS = 6
TOKEN_CHUNK_SIZE = 80
RAW_CONTEXT_TAIL_MESSAGES = 8


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
    compression_config = get_context_compression_config()
    messages = await fetch_agent_context(websocket, session_id, compression_config)
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
            messages = await compress_loop_context_if_needed(
                websocket, messages, compression_config
            )
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
    result = await run_tool(name, arguments, agent_workspace())
    await websocket.send_json(
        {
            "type": "tool_result",
            "tool": result.name,
            "success": result.success,
            "content": result.content,
        }
    )
    messages.append(tool_result_for_provider(tool_call, result))


async def fetch_agent_context(
    websocket: WebSocket,
    session_id: str,
    compression_config: ContextCompressionConfig,
) -> list[dict[str, Any]]:
    if not compression_config.enabled:
        return fetch_recent_agent_context(session_id)

    system_message = {
        "role": "system",
        "content": agent_system_prompt(),
    }
    summary = fetch_context_summary(session_id)
    through_sequence = int(summary["through_sequence"]) if summary else 0
    rows = get_messages_after_sequence(session_id, through_sequence)
    messages = build_context_messages(system_message, summary, rows)

    if context_char_count(messages) <= compression_config.threshold_chars:
        return messages

    if len(rows) <= RAW_CONTEXT_TAIL_MESSAGES:
        return messages

    before_chars = context_char_count(messages)
    compress_rows = rows[:-RAW_CONTEXT_TAIL_MESSAGES]
    tail_rows = rows[-RAW_CONTEXT_TAIL_MESSAGES:]
    summary_content = await create_context_summary(
        title="Conversation history compression",
        existing_summary=summary["content"] if summary else "",
        content=history_rows_text(compress_rows),
        target_chars=compression_config.target_chars,
    )
    through_sequence = int(compress_rows[-1]["sequence"])
    stored_summary = upsert_context_summary(
        session_id=session_id,
        content=summary_content,
        through_sequence=through_sequence,
    )
    compressed_messages = build_context_messages(system_message, stored_summary, tail_rows)
    await send_context_compressed_event(
        websocket=websocket,
        scope="history",
        before_chars=before_chars,
        after_chars=context_char_count(compressed_messages),
        summary_chars=len(summary_content),
        compressed_messages=len(compress_rows),
        through_sequence=through_sequence,
    )
    return compressed_messages


def fetch_recent_agent_context(session_id: str) -> list[dict[str, Any]]:
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


def build_context_messages(
    system_message: dict[str, Any],
    summary: dict[str, Any] | None,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    messages = [system_message]
    if summary and summary.get("content"):
        messages.append(summary_message(summary["content"], summary["through_sequence"]))

    for row in rows:
        messages.append(message_from_row(row))

    return messages


def message_from_row(row: dict[str, Any]) -> dict[str, str]:
    role = "assistant" if row["role"] == "agent" else "user"
    return {"role": role, "content": row["content"]}


def summary_message(content: str, through_sequence: int) -> dict[str, str]:
    return {
        "role": "system",
        "content": (
            "Compressed conversation summary through visible message sequence "
            f"{through_sequence}:\n{content}"
        ),
    }


async def compress_loop_context_if_needed(
    websocket: WebSocket,
    messages: list[dict[str, Any]],
    compression_config: ContextCompressionConfig,
) -> list[dict[str, Any]]:
    if not compression_config.enabled:
        return messages

    before_chars = context_char_count(messages)
    if before_chars <= compression_config.threshold_chars:
        return messages

    start_index = latest_tool_protocol_start(messages)
    if start_index is None:
        return messages

    loop_messages = messages[start_index:]
    summary_content = await create_context_summary(
        title="Recent tool activity compression",
        existing_summary="",
        content=messages_text(loop_messages),
        target_chars=compression_config.target_chars,
    )
    compressed_messages = [
        *messages[:start_index],
        {
            "role": "system",
            "content": f"Compressed recent tool activity summary:\n{summary_content}",
        },
    ]
    await send_context_compressed_event(
        websocket=websocket,
        scope="loop",
        before_chars=before_chars,
        after_chars=context_char_count(compressed_messages),
        summary_chars=len(summary_content),
        compressed_messages=len(loop_messages),
        through_sequence=None,
    )
    return compressed_messages


def latest_tool_protocol_start(messages: list[dict[str, Any]]) -> int | None:
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.get("role") == "assistant" and message.get("tool_calls"):
            return index

    return None


async def create_context_summary(
    *,
    title: str,
    existing_summary: str,
    content: str,
    target_chars: int,
) -> str:
    summary_request = [
        {
            "role": "system",
            "content": (
                "You compress agent conversation context. Return only a concise "
                "structured summary. Preserve user goals, explicit requirements, "
                "completed and failed actions, file paths, commands, errors, "
                "decisions, pending work, uncertainty, and anything the agent "
                "must not claim as completed. Do not invent facts."
            ),
        },
        {
            "role": "user",
            "content": (
                f"{title}\nTarget maximum length: {target_chars} characters.\n\n"
                f"Existing summary:\n{existing_summary or '(none)'}\n\n"
                f"Content to compress:\n{content}"
            ),
        },
    ]
    response = await create_llm_response(summary_request, tools=None)
    summary = response.get("content")
    if not isinstance(summary, str) or not summary.strip():
        raise AgentProviderError("LLM provider returned an empty context summary.")

    return summary.strip()


def history_rows_text(rows: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        f"[sequence={row['sequence']} role={row['role']}]\n{row['content']}"
        for row in rows
    )


def messages_text(messages: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        json.dumps(message, ensure_ascii=False, sort_keys=True) for message in messages
    )


def context_char_count(messages: list[dict[str, Any]]) -> int:
    return sum(len(json.dumps(message, ensure_ascii=False)) for message in messages)


async def send_context_compressed_event(
    *,
    websocket: WebSocket,
    scope: str,
    before_chars: int,
    after_chars: int,
    summary_chars: int,
    compressed_messages: int,
    through_sequence: int | None,
) -> None:
    event: dict[str, Any] = {
        "type": "context_compressed",
        "scope": scope,
        "before_chars": before_chars,
        "after_chars": after_chars,
        "summary_chars": summary_chars,
        "compressed_messages": compressed_messages,
    }
    if through_sequence is not None:
        event["through_sequence"] = through_sequence

    await websocket.send_json(event)


def agent_system_prompt() -> str:
    return (
        f"{get_system_prompt()}\n\n"
        f"Current workspace: {agent_workspace()}\n\n"
        "You can use placeholder tools to simulate agent actions. Tool results "
        "are observations with simulated=true; they do not prove that files were "
        "read, commands were run, or edits were applied. Use the tool results to "
        "plan and explain, and be explicit when an observation is simulated.\n\n"
        "You can also use run_bash to execute real bash commands inside the "
        "workspace. run_bash results have simulated=false and may have command "
        "side effects. Prefer run_bash for checks and tests, but do not claim "
        "that files were edited unless a real edit tool is added and reports "
        "that edit.\n\n"
        "For code or text search, prefer the rg tool first. It automatically "
        "falls back to grep and then to run_bash when needed. Use grep directly "
        "only when grep behavior is specifically required.\n\n"
        "Use read_file to inspect exact file contents and write_file only when "
        "the user explicitly asks you to create or change files. Both operate "
        "on real workspace files and return simulated=false."
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

    reasoning_content = message.get("reasoning_content")
    if isinstance(reasoning_content, str):
        provider_message["reasoning_content"] = reasoning_content

    return provider_message


def tool_result_for_provider(
    tool_call: dict[str, Any], result: ToolResult
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
