from typing import Any, Iterable

from automata_api.agent.backends.base import Backend

from ._core import ToolResult, json_response, parse_tool_arguments
from .base import AgentTool
from .bash import RunBashTool, run_bash_tool
from .exec_command import ExecCommandTool, exec_command_tool
from .files import ReadFileTool, WriteFileTool, read_file_tool, write_file_tool
from .patch import (
    ApplyPatchPreviewTool,
    ApplyPatchTool,
    apply_patch_preview_tool,
    apply_patch_tool,
)
from .search import GrepTool, RgTool, grep_tool, rg_tool


REGISTERED_TOOLS: tuple[AgentTool, ...] = (
    rg_tool,
    grep_tool,
    exec_command_tool,
    run_bash_tool,
    read_file_tool,
    write_file_tool,
    apply_patch_tool,
    apply_patch_preview_tool,
)


class ToolRegistry:
    def __init__(self, tools: Iterable[AgentTool]) -> None:
        self._tools = tuple(tools)
        self._tools_by_name = build_tool_index(self._tools)

    def specs(self, *, read_only_only: bool = False) -> list[dict[str, Any]]:
        return [
            tool.spec()
            for tool in self._tools
            if not read_only_only or tool.read_only
        ]

    def allowed_names(self, *, read_only_only: bool = False) -> set[str]:
        return {
            tool.name
            for tool in self._tools
            if not read_only_only or tool.read_only
        }

    async def run(
        self,
        name: str,
        raw_arguments: str | dict[str, Any] | None,
        *,
        mode: str = "act",
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

        tool = self._tools_by_name.get(name)
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

        return await tool.run_in_mode(arguments, mode=mode)

    async def dispatch(
        self,
        name: str,
        raw_arguments: str | dict[str, Any] | None,
        *,
        mode: str = "act",
    ) -> ToolResult:
        return await self.run(name, raw_arguments, mode=mode)

    async def run_authorized(
        self,
        name: str,
        raw_arguments: str | dict[str, Any] | None,
        *,
        mode: str = "act",
    ) -> ToolResult:
        arguments, parse_error = parse_tool_arguments(raw_arguments)
        if parse_error:
            return await self.run(name, raw_arguments, mode=mode)
        tool = self._tools_by_name.get(name)
        if tool is None:
            return await self.run(name, arguments, mode=mode)
        return await tool.run_authorized(arguments, mode=mode)


def default_tools(backend: Backend) -> tuple[AgentTool, ...]:
    return (
        RgTool(backend),
        GrepTool(backend),
        ExecCommandTool(backend),
        RunBashTool(backend),
        ReadFileTool(backend),
        WriteFileTool(backend),
        ApplyPatchTool(backend),
        ApplyPatchPreviewTool(backend),
    )


def registered_tools() -> tuple[AgentTool, ...]:
    return REGISTERED_TOOLS


def tool_for_name(name: str) -> AgentTool | None:
    return TOOLS_BY_NAME.get(name)


def build_tool_index(tools: Iterable[object]) -> dict[str, AgentTool]:
    index: dict[str, AgentTool] = {}
    for tool in tools:
        if not isinstance(tool, AgentTool):
            raise TypeError(f"Registered tool must extend AgentTool: {tool!r}")
        if not isinstance(tool.name, str) or not tool.name.strip():
            raise ValueError(f"Registered tool name must be a non-empty string: {tool!r}")
        if tool.name in index:
            raise ValueError(f"Duplicate tool registered: {tool.name}")
        index[tool.name] = tool
    return index


TOOLS_BY_NAME = build_tool_index(REGISTERED_TOOLS)
