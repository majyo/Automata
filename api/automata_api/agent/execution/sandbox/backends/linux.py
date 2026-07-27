from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from automata_api.agent.execution.sandbox.errors import SandboxError
from automata_api.agent.execution.sandbox.model import ProcessLaunchRequest


class LinuxSandboxBackend:
    name = "linux-bwrap"

    async def spawn(self, request: ProcessLaunchRequest) -> Any:
        bwrap = shutil.which("bwrap")
        if bwrap is None:
            raise SandboxError(
                "sandbox_unavailable",
                "Default permissions require Bubblewrap, but bwrap was not found.",
            )
        command = [
            bwrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-user-try",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup-try",
            "--cap-drop",
            "ALL",
            "--ro-bind",
            "/",
            "/",
            "--dev-bind",
            "/dev",
            "/dev",
            "--proc",
            "/proc",
        ]
        if request.profile.network == "restricted":
            command.append("--unshare-net")
        for root in request.profile.workspace_roots + request.profile.temporary_roots:
            path = Path(root)
            if path.exists():
                command.extend(("--bind", str(path), str(path)))
        for protected in request.profile.protected_paths:
            path = Path(protected)
            if path.exists():
                command.extend(("--ro-bind", str(path), str(path)))
        for denied in request.profile.deny_read_paths:
            path = Path(denied)
            if not path.exists():
                continue
            if path.is_dir():
                command.extend(("--tmpfs", str(path)))
            else:
                command.extend(("--ro-bind", "/dev/null", str(path)))
        command.extend(("--chdir", str(request.cwd), "--", *request.argv))
        return await asyncio.create_subprocess_exec(
            *command,
            cwd=str(request.cwd),
            env=dict(request.env),
            stdin=request.stdin,
            stdout=request.stdout,
            stderr=request.stderr,
            start_new_session=True,
        )
