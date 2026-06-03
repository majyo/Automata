import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import WebSocket

from automata_api.agent.llm import AgentProviderError
from automata_api.agent.runtime import stream_agent_loop, stream_plan_loop
from automata_api.config import AgentConfigurationError
from automata_api.repositories.agent_store import SessionAgentContextStore
from automata_api.repositories.sessions import (
    create_plan,
    mark_plan_executed,
    save_message,
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
        response = await forward_agent_events(
            websocket,
            stream_agent_loop(
                session_id=session_id,
                store=SessionAgentContextStore(),
                approved_plan_content=approved_plan_content,
            ),
        )
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
    if approved_plan_id:
        mark_plan_executed(session_id, approved_plan_id)
    await websocket.send_json({"type": "done", "message": message})


async def stream_plan_reply(
    websocket: WebSocket, session_id: str, prompt: str, prompt_message_id: str
) -> None:
    await websocket.send_json(
        {"type": "started", "session_id": session_id, "prompt": prompt, "mode": "plan"}
    )
    response = ""

    try:
        response = await forward_agent_events(
            websocket,
            stream_plan_loop(
                session_id=session_id,
                store=SessionAgentContextStore(),
            ),
        )
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
    plan = create_plan(
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


async def forward_agent_events(
    websocket: WebSocket, events: AsyncIterator[dict[str, Any]]
) -> str:
    response_parts: list[str] = []
    final_content = ""

    async for event in events:
        event_type = event["type"]
        if event_type == "token":
            content = event.get("content")
            if isinstance(content, str):
                response_parts.append(content)
            await websocket.send_json(event)
            continue

        if event_type == "final":
            content = event.get("content")
            if isinstance(content, str):
                final_content = content
            continue

        await websocket.send_json(event)

    return final_content or "".join(response_parts)
