from __future__ import annotations

import asyncio
import os
import subprocess
from typing import Any

from automata_api.agent.execution.sandbox.model import ProcessLaunchRequest


class DirectSandboxBackend:
    name = "direct"

    async def spawn(self, request: ProcessLaunchRequest) -> Any:
        group_kwargs: dict[str, Any]
        if os.name == "nt":
            group_kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        else:
            group_kwargs = {"start_new_session": True}
        return await asyncio.create_subprocess_exec(
            *request.argv,
            cwd=str(request.cwd),
            env=dict(request.env),
            stdin=request.stdin,
            stdout=request.stdout,
            stderr=request.stderr,
            **group_kwargs,
        )
