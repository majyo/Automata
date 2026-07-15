from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, TYPE_CHECKING

from automata_api.agent.execution.model import ToolRisk
from automata_api.agent.tools.base import AgentTool

if TYPE_CHECKING:
    from automata_api.agent.backends.base import Backend


class ToolExposure(str, Enum):
    DIRECT = "direct"
    DEFERRED = "deferred"
    HIDDEN = "hidden"


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    spec: dict[str, Any]
    executor: AgentTool
    read_only: bool
    risk: ToolRisk = "read"
    exposure: ToolExposure = ToolExposure.DIRECT
    source: str = "backend"
    search_text: str | None = None
    identity: str | None = None


@dataclass(frozen=True)
class ToolDiscoveryContext:
    session_id: str | None
    workspace: str | None
    backend: Backend | None
    mode: str
    config: Any = None


class ToolProvider(Protocol):
    def discover(self, context: ToolDiscoveryContext) -> tuple[ToolDescriptor, ...]:
        """Return tool descriptors available for this discovery context."""


class AsyncToolProvider(Protocol):
    async def discover(
        self, context: ToolDiscoveryContext
    ) -> tuple[ToolDescriptor, ...]:
        """Asynchronously return descriptors for this discovery context."""
