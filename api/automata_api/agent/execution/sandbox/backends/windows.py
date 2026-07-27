from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from automata_api.agent.execution.sandbox.errors import SandboxError
from automata_api.agent.execution.sandbox.model import ProcessLaunchRequest

_HOST_ENV = "AUTOMATA_SANDBOX_HOST"


class WindowsSandboxBackend:
    name = "windows-appcontainer"

    async def spawn(self, request: ProcessLaunchRequest) -> Any:
        host = find_windows_sandbox_host()
        if host is None:
            raise SandboxError(
                "sandbox_unavailable",
                "Default permissions require automata-sandbox-host.exe, but it was not found.",
            )
        profile_payload = request.profile.to_dict()
        profile_payload["runtime_roots"] = list(
            dict.fromkeys(
                (
                    *request.profile.runtime_roots,
                    *request.runtime_roots,
                    *_launch_runtime_roots(request),
                )
            )
        )
        payload = {
            "schema_version": 1,
            "argv": list(request.argv),
            "cwd": str(request.cwd),
            "env": dict(request.env),
            "profile": profile_payload,
        }
        request_path = write_windows_sandbox_request(payload)
        try:
            return await asyncio.create_subprocess_exec(
                str(host),
                "--request-file",
                str(request_path),
                cwd=str(request.cwd),
                env=dict(request.env),
                stdin=request.stdin,
                stdout=request.stdout,
                stderr=request.stderr,
                creationflags=0x00000200,
            )
        except OSError as error:
            request_path.unlink(missing_ok=True)
            raise SandboxError(
                "sandbox_spawn_failed",
                f"Failed to start Windows sandbox host: {error}",
            ) from error


def write_windows_sandbox_request(payload: dict[str, Any]) -> Path:
    request_dir = Path(tempfile.gettempdir()) / "automata-sandbox" / "requests"
    request_dir.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix="request-",
        suffix=".json",
        dir=request_dir,
        text=True,
    )
    path = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(
                payload,
                stream,
                ensure_ascii=True,
                separators=(",", ":"),
            )
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def _launch_runtime_roots(request: ProcessLaunchRequest) -> tuple[str, ...]:
    executable = Path(request.argv[0])
    if not executable.is_absolute():
        resolved = shutil.which(
            request.argv[0],
            path=request.env.get("PATH"),
        )
        if resolved is None:
            return ()
        executable = Path(resolved)
    executable = executable.resolve(strict=False)
    user_profile = Path(os.environ.get("USERPROFILE", Path.home())).resolve(
        strict=False
    )
    if executable.is_relative_to(user_profile):
        return (str(executable.parent),)
    return ()


def find_windows_sandbox_host() -> Path | None:
    configured = os.environ.get(_HOST_ENV, "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    executable_dir = Path(sys.executable).resolve().parent
    candidates.extend(
        (
            executable_dir / "automata-sandbox-host.exe",
            executable_dir / "binaries" / "automata-sandbox-host.exe",
        )
    )
    repository_root = Path(__file__).resolve().parents[6]
    candidates.extend(
        (
            repository_root
            / "native"
            / "windows-sandbox"
            / "target"
            / "release"
            / "automata-sandbox-host.exe",
            repository_root
            / "native"
            / "windows-sandbox"
            / "target"
            / "debug"
            / "automata-sandbox-host.exe",
        )
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)
