from typing import Any

from ._core import *  # noqa: F403
from ._core import ToolResult, json_response, parse_tool_arguments
from .registry import REGISTERED_TOOLS, registered_tools, tool_for_name


def tool_specs() -> list[dict[str, Any]]:
    return [tool.spec() for tool in registered_tools()]


async def run_tool(
    name: str, raw_arguments: str | dict[str, Any] | None, workspace: str
) -> ToolResult:
    arguments, parse_error = parse_tool_arguments(raw_arguments)
    if parse_error:
        return ToolResult(
            name=name,
            arguments={},
            content=json_response(
                {
                    "simulated": False,
                    "tool": name,
                    "ok": False,
                    "error": parse_error,
                }
            ),
            success=False,
        )

    tool = tool_for_name(name)
    if tool is None:
        return ToolResult(
            name=name,
            arguments=arguments,
            content=json_response(
                {
                    "simulated": False,
                    "tool": name,
                    "ok": False,
                    "error": f"Unknown tool: {name}",
                }
            ),
            success=False,
        )

    return await tool.run(arguments, workspace)
