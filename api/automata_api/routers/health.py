from typing import Any

from fastapi import APIRouter

from automata_api.services.agent import agent_status


router = APIRouter()


@router.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "agent": agent_status()}
