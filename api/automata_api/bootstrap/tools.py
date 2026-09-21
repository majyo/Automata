"""Assembly helpers for tools bound to a local workspace."""

from typing import Any

from automata_api.core.tools.models import ToolResult
from automata_api.core.tools.registry import ToolRegistry, default_tools
from automata_api.core.tools.router import ToolRouter


async def run_tool(
    name: str, raw_arguments: str | dict[str, Any] | None, workspace: str
) -> ToolResult:
    from automata_api.infrastructure.workspace.backends.local import LocalBackend

    registry = ToolRegistry(default_tools(LocalBackend(workspace)))
    return await registry.run(name, raw_arguments)


def local_tool_router(workspace: str) -> ToolRouter:
    from automata_api.infrastructure.workspace.backends.local import LocalBackend

    return ToolRouter.from_backend(LocalBackend(workspace), workspace=workspace)
