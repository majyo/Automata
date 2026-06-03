from typing import Iterable

from .base import AgentTool
from .bash import run_bash_tool
from .files import read_file_tool, write_file_tool
from .patch import apply_patch_preview_tool, apply_patch_tool
from .search import grep_tool, rg_tool


REGISTERED_TOOLS: tuple[AgentTool, ...] = (
    rg_tool,
    grep_tool,
    run_bash_tool,
    read_file_tool,
    write_file_tool,
    apply_patch_tool,
    apply_patch_preview_tool,
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
