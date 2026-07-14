from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from automata_api.repositories.sessions import (
    PlanNotFoundError,
    PlanStateError,
    SessionNotFoundError,
    approve_plan,
    save_context_message,
    save_message,
    session_exists,
)
from automata_api.agent.status import agent_ready_message
from automata_api.services.chat import (
    receive_payload,
    run_repository_call,
    stream_approved_plan_reply,
    stream_agent_reply,
    stream_plan_reply,
)


router = APIRouter()


@router.websocket("/ws/chat")
async def chat(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_json({"type": "ready", "message": agent_ready_message()})

    try:
        while True:
            payload = await receive_payload(websocket)
            payload_type = payload.get("type")
            if payload_type == "approve_plan":
                await handle_plan_approval(websocket, payload)
                continue

            if payload_type != "prompt":
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

            if not await run_repository_call(session_exists, session_id):
                await websocket.send_json(
                    {"type": "error", "message": "Session not found"}
                )
                continue

            user_message = await run_repository_call(
                save_message,
                session_id=session_id, role="user", content=prompt
            )
            await run_repository_call(
                save_context_message,
                session_id=session_id,
                message={"role": "user", "content": prompt},
            )
            if str(payload.get("mode", "")).strip() == "plan":
                await stream_plan_reply(
                    websocket,
                    session_id,
                    prompt,
                    user_message["id"],
                    payload.get("skills"),
                )
            else:
                await stream_agent_reply(
                    websocket,
                    session_id,
                    prompt,
                    payload.get("skills"),
                )
    except WebSocketDisconnect:
        return


async def handle_plan_approval(websocket: WebSocket, payload: dict) -> None:
    session_id = str(payload.get("session_id", "")).strip()
    plan_id = str(payload.get("plan_id", "")).strip()
    if not session_id or not plan_id:
        await websocket.send_json(
            {"type": "plan_error", "message": "Missing session_id or plan_id"}
        )
        return

    try:
        plan = await run_repository_call(approve_plan, session_id, plan_id)
    except SessionNotFoundError:
        await websocket.send_json({"type": "plan_error", "message": "Session not found"})
        return
    except PlanNotFoundError:
        await websocket.send_json({"type": "plan_error", "message": "Plan not found"})
        return
    except PlanStateError as error:
        await websocket.send_json({"type": "plan_error", "message": str(error)})
        return

    await stream_approved_plan_reply(websocket, session_id, plan)
