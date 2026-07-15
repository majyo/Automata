from abc import ABC, abstractmethod
from typing import Any

from ._core import ToolResult


class AgentTool(ABC):
    name: str
    read_only: bool = False

    @abstractmethod
    def spec(self) -> dict[str, Any]:
        """Return the LLM function-call spec for this tool."""

    @abstractmethod
    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        """Execute this tool."""

    async def run_in_mode(
        self, arguments: dict[str, Any], *, mode: str
    ) -> ToolResult:
        del mode
        return await self.run(arguments)

    async def run_authorized(
        self, arguments: dict[str, Any], *, mode: str
    ) -> ToolResult:
        return await self.run_in_mode(arguments, mode=mode)
