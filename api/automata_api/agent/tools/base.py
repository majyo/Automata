from abc import ABC, abstractmethod
from typing import Any, ClassVar

from ._core import ToolResult


class AgentTool(ABC):
    name: ClassVar[str]
    read_only: ClassVar[bool] = False

    @abstractmethod
    def spec(self) -> dict[str, Any]:
        """Return the LLM function-call spec for this tool."""

    @abstractmethod
    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        """Execute this tool."""
