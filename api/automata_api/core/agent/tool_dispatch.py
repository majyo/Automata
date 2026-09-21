"""Tool execution port owned by the turn engine."""

from __future__ import annotations

from typing import Any, Protocol

from automata_api.core.tools.models import ToolResult


class RunTool(Protocol):
    """Executes one builtin tool by name."""

    async def __call__(
        self, name: str, arguments: str | dict[str, Any] | None, workspace: str
    ) -> ToolResult: ...
