from typing import Any

from ._core import ToolResult, run_exec_command
from .base import AgentTool


class ExecCommandTool(AgentTool):
    name = "exec_command"

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Run a real shell command in the local workspace. Choose "
                    "shell=bash for POSIX shell scripts and shell=powershell "
                    "for PowerShell scripts. Cwd is restricted to the workspace "
                    "and output is bounded."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cmd": {
                            "type": "string",
                            "description": "Shell script to execute.",
                        },
                        "shell": {
                            "type": "string",
                            "enum": ["bash", "powershell"],
                            "description": (
                                "Shell dialect used to interpret cmd. Defaults "
                                "to bash. Use powershell for PowerShell-specific "
                                "scripts."
                            ),
                        },
                        "workdir": {
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
                        "max_output_chars": {
                            "type": "integer",
                            "description": (
                                "Optional character limit applied to stdout, "
                                "stderr, and combined output. Defaults to 20000 "
                                "and is capped at 60000."
                            ),
                        },
                    },
                    "required": ["cmd"],
                },
            },
        }

    async def run(self, arguments: dict[str, Any], workspace: str) -> ToolResult:
        return await run_exec_command(arguments, workspace)


exec_command_tool = ExecCommandTool()
