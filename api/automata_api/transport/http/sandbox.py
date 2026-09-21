from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from automata_api.core.tools.management import SandboxAdministration
from automata_api.core.tools.sandbox import SandboxError
from automata_api.transport.dependencies import sandbox_administration

router = APIRouter(prefix="/sandbox", tags=["sandbox"])


class SandboxSetupRequest(BaseModel):
    workspace: str


@router.get("/status")
async def sandbox_status(
    service: SandboxAdministration = Depends(sandbox_administration),
) -> dict[str, Any]:
    return service.status()


@router.post("/setup")
async def sandbox_setup(
    request: SandboxSetupRequest,
    service: SandboxAdministration = Depends(sandbox_administration),
) -> dict[str, Any]:
    workspace = Path(request.workspace).expanduser().resolve(strict=False)
    if not workspace.is_dir():
        raise HTTPException(status_code=400, detail="Workspace must be a directory.")
    try:
        return await service.prepare(str(workspace))
    except SandboxError as error:
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": error.code,
                "message": error.public_message,
            },
        ) from error
