from fastapi import APIRouter, WebSocket

from automata_api.services.connection import AgentConnection
from automata_api.transport.dependencies import container_from_websocket

router = APIRouter()


@router.websocket("/ws/chat")
async def chat(websocket: WebSocket) -> None:
    container = container_from_websocket(websocket)
    await AgentConnection(
        websocket,
        coordinator=container.coordinator,
        event_hub=container.event_hub,
    ).serve()
