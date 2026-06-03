from typing import Any

from ._core import ToolResult, run_bash
from .base import AgentTool


class RunBashTool(AgentTool):
    name = "run_bash"

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Run a real bash command in the local workspace. This can "
                    "read files, run tests, and perform command side effects. "
                    "The command is executed with bash -lc and cwd is restricted "
                    "to the workspace."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Command to execute via bash -lc.",
                        },
                        "cwd": {
                            "type": "string",
                            "description": (
                                "Optional workspace-relative directory to run in. "
                                "Defaults to the workspace root."
                            ),
                        },
                        "timeout_seconds": {
                            "type": "number",
                            "description": (
                                "Optional timeout in seconds. Defaults to 30 and "
                                "is capped at 120."
                            ),
                        },
                    },
                    "required": ["command"],
                },
            },
        }

    async def run(self, arguments: dict[str, Any], workspace: str) -> ToolResult:
        return await run_bash(arguments, workspace)


run_bash_tool = RunBashTool()
