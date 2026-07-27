from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from automata_api.agent.execution.permissions import (
    CompiledPermissionProfile,
    compile_permission_profile,
)
from automata_api.agent.execution.sandbox.environment import build_tool_environment
from automata_api.agent.execution.sandbox.errors import SandboxError
from automata_api.agent.execution.sandbox.manager import SandboxManager, sandbox_manager
from automata_api.agent.execution.sandbox.model import (
    ProcessLaunchRequest,
    SandboxMetadata,
)


class ProcessLauncher:
    def __init__(self, manager: SandboxManager = sandbox_manager) -> None:
        self.manager = manager

    async def spawn(
        self,
        *argv: str,
        cwd: str | Path,
        env: Mapping[str, str] | None = None,
        stdin: Any = None,
        stdout: Any = None,
        stderr: Any = None,
        profile: CompiledPermissionProfile | None = None,
        scope_name: str = "tool",
        runtime_roots: tuple[str | Path, ...] = (),
        explicit_env_names: tuple[str, ...] = (),
    ) -> Any:
        if not argv:
            raise ValueError("Process argv must not be empty.")
        resolved_cwd = Path(cwd).expanduser().resolve(strict=False)
        resolved_profile = profile or current_permission_profile(resolved_cwd)
        attempt = current_sandbox_attempt()
        backend = self.manager.select(resolved_profile)
        tool_env = build_tool_environment(
            resolved_profile,
            os.environ if env is None else env,
            explicit_names=explicit_env_names,
        )
        request = ProcessLaunchRequest(
            argv=tuple(argv),
            cwd=resolved_cwd,
            env=tool_env,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            profile=resolved_profile,
            scope_name=scope_name,
            runtime_roots=tuple(
                str(Path(root).expanduser().resolve(strict=False))
                for root in runtime_roots
            ),
        )
        await _emit_sandbox_event(
            {
                "type": "sandbox_selected",
                "backend": backend.name,
                "profile_hash": resolved_profile.profile_hash,
                "profile_version": resolved_profile.version,
                "enforcement": resolved_profile.sandbox_enforcement,
                "attempt": attempt,
            }
        )
        await _emit_sandbox_event(
            {
                "type": "sandbox_attempt_started",
                "backend": backend.name,
                "profile_hash": resolved_profile.profile_hash,
                "attempt": attempt,
            }
        )
        try:
            process = await backend.spawn(request)
        except SandboxError as error:
            await emit_sandbox_event(
                {
                    "type": (
                        "sandbox_setup_required"
                        if error.code == "sandbox_setup_required"
                        else "sandbox_attempt_failed"
                    ),
                    "backend": backend.name,
                    "profile_hash": resolved_profile.profile_hash,
                    "attempt": attempt,
                    "error_code": error.code,
                }
            )
            raise
        metadata = SandboxMetadata(
            enforcement=resolved_profile.sandbox_enforcement,
            backend=backend.name,
            profile_hash=resolved_profile.profile_hash,
            attempt=attempt,
        )
        setattr(process, "automata_sandbox", metadata)
        return process


def current_permission_profile(cwd: Path) -> CompiledPermissionProfile:
    from automata_api.agent.execution.process import current_process_scope

    scope = current_process_scope()
    if scope is not None and scope.permission_profile is not None:
        return scope.permission_profile
    return compile_permission_profile("full_access", workspace=cwd)


def current_sandbox_attempt() -> int:
    from automata_api.agent.execution.process import current_process_scope

    scope = current_process_scope()
    return scope.sandbox_attempt if scope is not None else 1


async def emit_sandbox_event(payload: dict[str, Any]) -> None:
    from automata_api.agent.execution.process import current_process_scope

    scope = current_process_scope()
    if scope is not None and scope.emit_event is not None:
        await scope.emit_event(payload)


_emit_sandbox_event = emit_sandbox_event


process_launcher = ProcessLauncher()
