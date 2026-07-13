import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import WebSocket

from automata_api.agent.backends.factory import (
    BackendConfigurationError,
    create_backend,
)
from automata_api.agent.llm import AgentProviderError
from automata_api.agent.mcp.runtime import create_mcp_tool_runtime
from automata_api.agent.runtime import stream_agent_loop, stream_plan_loop
from automata_api.config import AgentConfigurationError
from automata_api.repositories.agent_store import SessionAgentContextStore
from automata_api.repositories.sessions import (
    SessionNotFoundError,
    create_plan,
    mark_plan_executed,
    save_message,
    save_tool_run_message,
    session_backend_config,
    update_tool_run_result,
)
from automata_api.schemas import ChatPayload


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
    websocket: WebSocket,
    session_id: str,
    prompt: str,
    approved_plan_content: str | None = None,
    approved_plan_id: str | None = None,
) -> None:
    await websocket.send_json(
        {"type": "started", "session_id": session_id, "prompt": prompt}
    )
    response = ""

    try:
        session_config = await run_repository_call(session_backend_config, session_id)
        backend = create_backend(
            session_config["backend"],
            workspace=session_config["working_directory"],
        )
        async with backend:
            async with create_mcp_tool_runtime(
                backend=backend,
                session_id=session_id,
                workspace=session_config["working_directory"],
                mode="act",
            ) as mcp_runtime:
                await send_mcp_runtime_events(websocket, mcp_runtime)
                response = await forward_agent_events(
                    session_id=session_id,
                    websocket=websocket,
                    events=stream_agent_loop(
                        session_id=session_id,
                        store=SessionAgentContextStore(),
                        workspace=session_config["working_directory"],
                        workspace_label=backend.workspace_label,
                        router=mcp_runtime.router,
                        tool_notes=backend.prompt_notes(),
                        approved_plan_content=approved_plan_content,
                    ),
                )
    except SessionNotFoundError as error:
        await websocket.send_json({"type": "error", "message": str(error)})
        return
    except BackendConfigurationError as error:
        await websocket.send_json({"type": "error", "message": str(error)})
        return
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

    message = await run_repository_call(
        save_message, session_id=session_id, role="agent", content=response
    )
    if approved_plan_id:
        await run_repository_call(mark_plan_executed, session_id, approved_plan_id)
    await websocket.send_json({"type": "done", "message": message})


async def stream_plan_reply(
    websocket: WebSocket, session_id: str, prompt: str, prompt_message_id: str
) -> None:
    await websocket.send_json(
        {"type": "started", "session_id": session_id, "prompt": prompt, "mode": "plan"}
    )
    response = ""

    try:
        session_config = await run_repository_call(session_backend_config, session_id)
        backend = create_backend(
            session_config["backend"],
            workspace=session_config["working_directory"],
        )
        async with backend:
            async with create_mcp_tool_runtime(
                backend=backend,
                session_id=session_id,
                workspace=session_config["working_directory"],
                mode="plan",
            ) as mcp_runtime:
                await send_mcp_runtime_events(websocket, mcp_runtime)
                response = await forward_agent_events(
                    session_id=session_id,
                    websocket=websocket,
                    events=stream_plan_loop(
                        session_id=session_id,
                        store=SessionAgentContextStore(),
                        workspace=session_config["working_directory"],
                        workspace_label=backend.workspace_label,
                        router=mcp_runtime.router,
                        tool_notes=backend.prompt_notes(),
                    ),
                )
    except SessionNotFoundError as error:
        await websocket.send_json({"type": "error", "message": str(error)})
        return
    except BackendConfigurationError as error:
        await websocket.send_json({"type": "error", "message": str(error)})
        return
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

    message = await run_repository_call(
        save_message, session_id=session_id, role="agent", content=response
    )
    plan = await run_repository_call(
        create_plan,
        session_id=session_id,
        prompt_message_id=prompt_message_id,
        plan_message_id=message["id"],
        content=response,
    )
    await websocket.send_json(
        {
            "type": "plan_ready",
            "session_id": session_id,
            "plan_id": plan["id"],
            "status": plan["status"],
            "content": response,
        }
    )
    await websocket.send_json({"type": "done", "message": message})


async def stream_approved_plan_reply(
    websocket: WebSocket, session_id: str, plan: dict[str, Any]
) -> None:
    plan_id = str(plan["id"])
    await websocket.send_json(
        {"type": "plan_approved", "session_id": session_id, "plan_id": plan_id}
    )
    await stream_agent_reply(
        websocket=websocket,
        session_id=session_id,
        prompt=f"Approved plan {plan_id}",
        approved_plan_content=str(plan["content"]),
        approved_plan_id=plan_id,
    )


async def send_mcp_runtime_events(websocket: WebSocket, runtime) -> None:
    for warning in runtime.warnings:
        await websocket.send_json(
            {
                "type": "mcp_server_status",
                "status": "warning",
                "message": warning,
            }
        )
    for candidate in runtime.candidates:
        await websocket.send_json(
            {
                "type": "mcp_server_candidate",
                "server": candidate.name,
                "provenance": candidate.provenance,
                "fingerprint": candidate.fingerprint,
            }
        )


async def forward_agent_events(
    session_id: str, websocket: WebSocket, events: AsyncIterator[dict[str, Any]]
) -> str:
    pending_agent_parts: list[str] = []
    final_content = ""
    tool_run_message_ids: dict[str, str] = {}

    async for event in events:
        event_type = event["type"]
        if event_type == "token":
            content = event.get("content")
            if isinstance(content, str):
                pending_agent_parts.append(content)
            await websocket.send_json(event)
            continue

        if event_type == "final":
            content = event.get("content")
            if isinstance(content, str):
                final_content = content
            continue

        if event_type == "tool_call":
            await save_pending_agent_message(session_id, pending_agent_parts)
            tool_call_id = tool_call_id_from_event(event)
            message = await run_repository_call(
                save_tool_run_message,
                session_id=session_id,
                tool_call_id=tool_call_id,
                tool=tool_name_from_event(event),
                arguments=arguments_from_event(event),
            )
            tool_run_message_ids[tool_call_id] = str(message["id"])
            await websocket.send_json(event)
            continue

        if event_type == "tool_result":
            tool_call_id = tool_call_id_from_event(event)
            message_id = tool_run_message_ids.get(tool_call_id)
            if message_id:
                await run_repository_call(
                    update_tool_run_result,
                    session_id=session_id,
                    message_id=message_id,
                    success=event.get("success") is not False,
                    content=content_from_event(event),
                )
            else:
                message = await run_repository_call(
                    save_tool_run_message,
                    session_id=session_id,
                    tool_call_id=tool_call_id,
                    tool=tool_name_from_event(event),
                    arguments="{}",
                )
                await run_repository_call(
                    update_tool_run_result,
                    session_id=session_id,
                    message_id=str(message["id"]),
                    success=event.get("success") is not False,
                    content=content_from_event(event),
                )
            await websocket.send_json(event)
            continue

        await websocket.send_json(event)

    return final_content or "".join(pending_agent_parts)


async def save_pending_agent_message(session_id: str, parts: list[str]) -> None:
    content = "".join(parts)
    parts.clear()
    if not content.strip():
        return

    await run_repository_call(
        save_message, session_id=session_id, role="agent", content=content
    )


async def run_repository_call(function, /, *args, **kwargs):
    return await asyncio.to_thread(function, *args, **kwargs)


def tool_call_id_from_event(event: dict[str, Any]) -> str:
    tool_call_id = event.get("tool_call_id")
    return tool_call_id if isinstance(tool_call_id, str) and tool_call_id else "unknown_tool_call"


def tool_name_from_event(event: dict[str, Any]) -> str:
    tool = event.get("tool")
    return tool if isinstance(tool, str) and tool else "unknown_tool"


def arguments_from_event(event: dict[str, Any]) -> str:
    arguments = event.get("arguments")
    return arguments if isinstance(arguments, str) else "{}"


def content_from_event(event: dict[str, Any]) -> str:
    content = event.get("content")
    return content if isinstance(content, str) else ""
