from typing import Any

from automata_api.agent.backends.base import Backend

from ._core import ToolResult, exec_command_error_result, string_argument, timeout_argument
from .base import AgentTool


class ExecCommandTool(AgentTool):
    name = "exec_command"

    def __init__(self, backend: Backend | None = None) -> None:
        self.backend = backend

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
                    " Set yield_time_ms to create a non-PTY live process session "
                    "when the command is still running after the initial wait."
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
                        "yield_time_ms": {
                            "type": "integer",
                            "description": (
                                "Optional initial wait in milliseconds. When set, "
                                "a still-running command returns a process session "
                                "id for write_stdin. Capped at 30000."
                            ),
                        },
                    },
                    "required": ["cmd"],
                },
            },
        }

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        if self.backend is None:
            raise RuntimeError("Tool instance is not bound to a backend.")
        run_exec_command = getattr(self.backend, "run_exec_command", None)
        if run_exec_command is None:
            cmd = string_argument(arguments, "cmd", "")
            shell = string_argument(arguments, "shell", "bash")
            workdir = string_argument(arguments, "workdir", ".")
            return exec_command_error_result(
                arguments=arguments,
                cmd=cmd,
                shell=shell,
                workdir=workdir,
                cwd=self.backend.workspace_label,
                timeout_seconds=timeout_argument(arguments),
                error=f"exec_command is not supported by backend: {self.backend.kind}",
            )
        return await run_exec_command(arguments)


exec_command_tool = ExecCommandTool()
