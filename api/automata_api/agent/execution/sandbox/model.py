from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

from automata_api.agent.execution.permissions import CompiledPermissionProfile

StdioMode = Literal["inherit", "pipe", "null"]


@dataclass(frozen=True)
class ProcessLaunchRequest:
    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str]
    stdin: Any
    stdout: Any
    stderr: Any
    profile: CompiledPermissionProfile
    scope_name: str
    runtime_roots: tuple[str, ...] = ()


@dataclass(frozen=True)
class SandboxMetadata:
    enforcement: str
    backend: str
    profile_hash: str
    attempt: int = 1
    denied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "enforcement": self.enforcement,
            "backend": self.backend,
            "profile_hash": self.profile_hash,
            "attempt": self.attempt,
            "denied": self.denied,
        }
