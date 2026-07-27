from fastapi import APIRouter, WebSocket

from automata_api.services.connection import AgentConnection

router = APIRouter()


@router.websocket("/ws/chat")
async def chat(websocket: WebSocket) -> None:
    await AgentConnection(websocket).serve()
