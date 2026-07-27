from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from automata_api.agent.execution.permissions import (
    compile_run_permission_profile,
)
from automata_api.agent.execution.sandbox.errors import SandboxError
from automata_api.agent.execution.sandbox.setup import (
    prepare_windows_sandbox,
    windows_sandbox_status,
)

router = APIRouter(prefix="/sandbox", tags=["sandbox"])


class SandboxSetupRequest(BaseModel):
    workspace: str


@router.get("/status")
async def sandbox_status() -> dict[str, Any]:
    return windows_sandbox_status()


@router.post("/setup")
async def sandbox_setup(request: SandboxSetupRequest) -> dict[str, Any]:
    workspace = Path(request.workspace).expanduser().resolve(strict=False)
    if not workspace.is_dir():
        raise HTTPException(status_code=400, detail="Workspace must be a directory.")
    setup_key = hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()
    profile = compile_run_permission_profile(
        "default",
        workspace=workspace,
        run_id=f"sandbox-setup:{setup_key}",
    )
    try:
        return await prepare_windows_sandbox(
            profile,
            allow_elevation=True,
        )
    except SandboxError as error:
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": error.code,
                "message": error.public_message,
            },
        ) from error
