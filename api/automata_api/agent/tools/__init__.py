from typing import Any

from ._core import *  # noqa: F403
from ._core import ToolResult, json_response, parse_tool_arguments
from .registry import (
    REGISTERED_TOOLS,
    ToolRegistry,
    default_tools,
    registered_tools,
    tool_for_name,
)
from .model import (
    AsyncToolProvider,
    ToolDescriptor,
    ToolDiscoveryContext,
    ToolExposure,
    ToolProvider,
)
from .providers import BackendToolProvider, StaticToolProvider, descriptor_for_tool
from .router import ToolRouter, ToolRouterBuilder
from .tool_search import TOOL_SEARCH_NAME


def tool_specs() -> list[dict[str, Any]]:
    return [tool.spec() for tool in registered_tools()]


async def run_tool(
    name: str, raw_arguments: str | dict[str, Any] | None, workspace: str
) -> ToolResult:
    from automata_api.agent.backends.local import LocalBackend

    registry = ToolRegistry(default_tools(LocalBackend(workspace)))
    return await registry.run(name, raw_arguments)
