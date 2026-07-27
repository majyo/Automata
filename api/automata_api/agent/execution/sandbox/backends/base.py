from __future__ import annotations

from typing import Any, Protocol

from automata_api.agent.execution.sandbox.model import ProcessLaunchRequest


class SandboxBackend(Protocol):
    name: str

    async def spawn(self, request: ProcessLaunchRequest) -> Any: ...
