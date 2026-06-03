from typing import Any

from ._core import ToolResult, run_grep, run_rg
from .base import AgentTool


class RgTool(AgentTool):
    name = "rg"

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Search files in the workspace. Prefer this over grep. It "
                    "tries ripgrep first, falls back to grep, then falls back to "
                    "run_bash with a suitable search command for the environment."
                ),
                "parameters": search_parameters(),
            },
        }

    async def run(self, arguments: dict[str, Any], workspace: str) -> ToolResult:
        return await run_rg(arguments, workspace)


class GrepTool(AgentTool):
    name = "grep"

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Search files in the workspace using grep semantics. Use rg "
                    "first unless grep is specifically needed. Falls back to "
                    "run_bash with grep when native grep is unavailable."
                ),
                "parameters": search_parameters(),
            },
        }

    async def run(self, arguments: dict[str, Any], workspace: str) -> ToolResult:
        return await run_grep(arguments, workspace)


def search_parameters() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Search pattern.",
            },
            "path": {
                "type": "string",
                "description": (
                    "Optional workspace-relative file or directory to search. "
                    "Defaults to the workspace root."
                ),
            },
            "cwd": {
                "type": "string",
                "description": (
                    "Optional workspace-relative directory to run in. Defaults "
                    "to the workspace root."
                ),
            },
            "timeout_seconds": {
                "type": "number",
                "description": (
                    "Optional timeout in seconds. Defaults to 30 and is capped "
                    "at 120."
                ),
            },
        },
        "required": ["pattern"],
    }


rg_tool = RgTool()
grep_tool = GrepTool()
