from abc import ABC, abstractmethod
from typing import Any, ClassVar

from ._core import ToolResult


class AgentTool(ABC):
    name: ClassVar[str]

    @abstractmethod
    def spec(self) -> dict[str, Any]:
        """Return the LLM function-call spec for this tool."""

    @abstractmethod
    async def run(self, arguments: dict[str, Any], workspace: str) -> ToolResult:
        """Execute this tool in the given workspace."""
