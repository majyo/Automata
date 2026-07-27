from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from automata_api.agent.execution.sandbox.errors import SandboxError
from automata_api.agent.execution.sandbox.model import ProcessLaunchRequest


class MacOSSandboxBackend:
    name = "macos-seatbelt"

    async def spawn(self, request: ProcessLaunchRequest) -> Any:
        executable = Path("/usr/bin/sandbox-exec")
        if not executable.is_file():
            raise SandboxError(
                "sandbox_unavailable",
                "Default permissions require /usr/bin/sandbox-exec.",
            )
        profile = _seatbelt_profile(request)
        return await asyncio.create_subprocess_exec(
            str(executable),
            "-p",
            profile,
            *request.argv,
            cwd=str(request.cwd),
            env=dict(request.env),
            stdin=request.stdin,
            stdout=request.stdout,
            stderr=request.stderr,
            start_new_session=True,
        )


def _seatbelt_profile(request: ProcessLaunchRequest) -> str:
    rules = [
        "(version 1)",
        "(deny default)",
        "(allow process*)",
        "(allow sysctl-read)",
        "(allow file-read*)",
    ]
    for root in request.profile.workspace_roots + request.profile.temporary_roots:
        rules.append(f'(allow file-write* (subpath "{_escape(root)}"))')
    for protected in request.profile.protected_paths:
        rules.append(f'(deny file-write* (subpath "{_escape(protected)}"))')
    for denied in request.profile.deny_read_paths:
        rules.append(f'(deny file-read* (subpath "{_escape(denied)}"))')
    if request.profile.network == "enabled":
        rules.append("(allow network*)")
    return "\n".join(rules)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
