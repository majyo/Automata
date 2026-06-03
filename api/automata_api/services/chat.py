import asyncio
import json
from typing import Any

import httpx
from fastapi import WebSocket

from automata_api.agent.llm import AgentProviderError
from automata_api.agent.runtime import run_agent_loop, run_plan_loop
from automata_api.config import AgentConfigurationError
from automata_api.repositories.agent_store import SessionAgentContextStore
from automata_api.repositories.sessions import (
    create_plan,
    mark_plan_executed,
    save_message,
)
from automata_api.schemas import ChatPayload


TOKEN_CHUNK_SIZE = 32
TOKEN_STREAM_DELAY_SECONDS = 0.025


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
        response = await run_agent_loop(
            session_id=session_id,
            store=SessionAgentContextStore(),
            emit_event=websocket.send_json,
            approved_plan_content=approved_plan_content,
        )
        for chunk in chunk_text(response):
            await websocket.send_json({"type": "token", "content": chunk})
            await asyncio.sleep(TOKEN_STREAM_DELAY_SECONDS)
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
        response = await run_plan_loop(
            session_id=session_id,
            store=SessionAgentContextStore(),
            emit_event=websocket.send_json,
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


def chunk_text(text: str) -> list[str]:
    return [
        text[index : index + TOKEN_CHUNK_SIZE]
        for index in range(0, len(text), TOKEN_CHUNK_SIZE)
    ]
