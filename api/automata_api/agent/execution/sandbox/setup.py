from __future__ import annotations

import asyncio
import base64
import os
from typing import Any

from automata_api.agent.execution.permissions import CompiledPermissionProfile
from automata_api.agent.execution.sandbox.backends.windows import (
    find_windows_sandbox_host,
    write_windows_sandbox_request,
)
from automata_api.agent.execution.sandbox.errors import SandboxError
from automata_api.agent.execution.sandbox.protocol import classify_sandbox_failure


async def prepare_windows_sandbox(
    profile: CompiledPermissionProfile,
    *,
    allow_elevation: bool,
) -> dict[str, Any]:
    if os.name != "nt":
        raise SandboxError(
            "sandbox_policy_unsupported",
            "Windows sandbox setup is only available on Windows.",
        )
    host = find_windows_sandbox_host()
    if host is None:
        raise SandboxError(
            "sandbox_unavailable",
            "automata-sandbox-host.exe was not found.",
        )
    payload = _prepare_payload(profile)
    request_path = write_windows_sandbox_request(payload)
    process = await asyncio.create_subprocess_exec(
        str(host),
        "--request-file",
        str(request_path),
        "--prepare-only",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=0x0800_0000,
    )
    stdout, stderr = await process.communicate()
    if process.returncode == 0:
        return _setup_result(profile, elevated=False)

    failure = classify_sandbox_failure(
        exit_code=process.returncode,
        stderr=stderr.decode("utf-8", errors="replace"),
        metadata=None,
    )
    if not allow_elevation:
        raise SandboxError(
            failure.code if failure is not None else "sandbox_setup_required",
            (
                failure.message
                if failure is not None
                else "Windows sandbox setup requires elevation."
            ),
        )

    request_path = write_windows_sandbox_request(payload)
    encoded_command = base64.b64encode(
        (
            "$ErrorActionPreference='Stop';"
            f"$p=Start-Process -FilePath '{_ps_quote(str(host))}' "
            "-ArgumentList @("
            "'--request-file',"
            f"'{_ps_quote(str(request_path))}',"
            "'--prepare-only'"
            ") -Verb RunAs -Wait -PassThru -WindowStyle Hidden;"
            "exit $p.ExitCode"
        ).encode("utf-16le")
    ).decode("ascii")
    elevation = await asyncio.create_subprocess_exec(
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-EncodedCommand",
        encoded_command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        creationflags=0x0800_0000,
    )
    try:
        _, elevation_stderr = await asyncio.wait_for(
            elevation.communicate(),
            timeout=180.0,
        )
    except TimeoutError as error:
        elevation.kill()
        await elevation.wait()
        request_path.unlink(missing_ok=True)
        raise SandboxError(
            "sandbox_timed_out",
            "Windows sandbox setup timed out.",
        ) from error
    request_path.unlink(missing_ok=True)
    if elevation.returncode != 0:
        message = elevation_stderr.decode("utf-8", errors="replace").strip()
        raise SandboxError(
            "sandbox_setup_failed",
            message or "Windows sandbox setup was cancelled or failed.",
        )
    return _setup_result(profile, elevated=True)


def windows_sandbox_status() -> dict[str, Any]:
    host = find_windows_sandbox_host() if os.name == "nt" else None
    return {
        "platform": "windows" if os.name == "nt" else os.name,
        "backend": "windows-appcontainer" if os.name == "nt" else None,
        "available": host is not None,
        "host_path": str(host) if host is not None else None,
    }


def _prepare_payload(profile: CompiledPermissionProfile) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "argv": ["cmd.exe", "/d", "/c", "exit", "0"],
        "cwd": profile.workspace_roots[0],
        "env": {},
        "profile": profile.to_dict(),
    }


def _setup_result(
    profile: CompiledPermissionProfile,
    *,
    elevated: bool,
) -> dict[str, Any]:
    return {
        "ready": True,
        "backend": "windows-appcontainer",
        "profile_hash": profile.profile_hash,
        "profile_version": profile.version,
        "elevated": elevated,
    }


def _ps_quote(value: str) -> str:
    return value.replace("'", "''")
