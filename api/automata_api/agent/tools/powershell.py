from __future__ import annotations

from typing import Any

from automata_api.agent.backends.base import Backend, BackendError

from ._core import ToolResult, json_response, string_argument, timeout_argument
from .base import AgentTool


class RunPowershellTool(AgentTool):
    name = "run_powershell"

    def __init__(self, backend: Backend | None = None) -> None:
        self.backend = backend

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Run a real PowerShell command in the local Windows workspace. "
                    "The command is executed with -NoProfile -NonInteractive and "
                    "cwd is restricted to the workspace."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Command to execute via PowerShell.",
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

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        if self.backend is None:
            raise RuntimeError("Tool instance is not bound to a backend.")

        command = string_argument(arguments, "command", "")
        timeout_seconds = timeout_argument(arguments)
        if not command:
            return powershell_error_result(
                arguments=arguments,
                command=command,
                cwd=self.backend.workspace_label,
                timeout_seconds=timeout_seconds,
                error="Missing required command.",
            )

        exec_powershell = getattr(self.backend, "exec_powershell", None)
        if exec_powershell is None:
            return powershell_error_result(
                arguments=arguments,
                command=command,
                cwd=self.backend.workspace_label,
                timeout_seconds=timeout_seconds,
                error=f"run_powershell is not supported by backend: {self.backend.kind}",
            )

        try:
            result = await exec_powershell(
                command,
                cwd=arguments.get("cwd"),
                timeout_seconds=timeout_seconds,
            )
        except BackendError as error:
            return powershell_error_result(
                arguments=arguments,
                command=command,
                cwd=error.cwd or self.backend.workspace_label,
                timeout_seconds=timeout_seconds,
                error=str(error),
                shell=error.shell,
            )

        payload = {
            "simulated": False,
            "ok": result.exit_code == 0,
            "command": command,
            "cwd": result.cwd,
            "shell": result.shell,
            "timeout_seconds": timeout_seconds,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "stdout_truncated": result.stdout_truncated,
            "stderr_truncated": result.stderr_truncated,
        }
        return ToolResult(
            name=self.name,
            arguments=arguments,
            content=json_response(payload),
            success=payload["ok"],
        )


def powershell_error_result(
    *,
    arguments: dict[str, Any],
    command: str,
    cwd: str,
    timeout_seconds: float,
    error: str,
    shell: str | None = None,
) -> ToolResult:
    return ToolResult(
        name="run_powershell",
        arguments=arguments,
        content=json_response(
            {
                "simulated": False,
                "ok": False,
                "command": command,
                "cwd": cwd,
                "shell": shell,
                "timeout_seconds": timeout_seconds,
                "exit_code": None,
                "timed_out": False,
                "stdout": "",
                "stderr": error,
                "stdout_truncated": False,
                "stderr_truncated": False,
            }
        ),
        success=False,
    )
