from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from automata_api.repositories.sessions import save_message, session_exists
from automata_api.services.agent import (
    agent_ready_message,
    receive_payload,
    stream_agent_reply,
)


router = APIRouter()


@router.websocket("/ws/chat")
async def chat(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_json({"type": "ready", "message": agent_ready_message()})

    try:
        while True:
            payload = await receive_payload(websocket)
            if payload.get("type") != "prompt":
                await websocket.send_json(
                    {"type": "error", "message": "Unsupported message type"}
                )
                continue

            session_id = str(payload.get("session_id", "")).strip()
            prompt = str(payload.get("prompt", "")).strip()
            if not session_id or not prompt:
                await websocket.send_json(
                    {"type": "error", "message": "Missing session_id or prompt"}
                )
                continue

            if not session_exists(session_id):
                await websocket.send_json(
                    {"type": "error", "message": "Session not found"}
                )
                continue

            save_message(session_id=session_id, role="user", content=prompt)
            await stream_agent_reply(websocket, session_id, prompt)
    except WebSocketDisconnect:
        return
