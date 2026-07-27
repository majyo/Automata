from typing import Any

from automata_api.agent.backends.base import Backend, BackendError

from ._core import (
    ToolResult,
    bash_error_result,
    json_response,
    string_argument,
    timeout_argument,
)
from .base import AgentTool


class RunBashTool(AgentTool):
    name = "run_bash"

    def __init__(self, backend: Backend | None = None) -> None:
        self.backend = backend

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

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        backend = require_backend(self.backend)
        command = string_argument(arguments, "command", "")
        timeout_seconds = timeout_argument(arguments)
        if not command:
            return bash_error_result(
                arguments=arguments,
                command=command,
                cwd=backend.workspace_label,
                timeout_seconds=timeout_seconds,
                error="Missing required command.",
            )

        try:
            result = await backend.exec_shell(
                command,
                cwd=arguments.get("cwd"),
                timeout_seconds=timeout_seconds,
            )
        except BackendError as error:
            return bash_error_result(
                arguments=arguments,
                command=command,
                cwd=error.cwd or backend.workspace_label,
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
            name="run_bash",
            arguments=arguments,
            content=json_response(payload),
            success=payload["ok"],
        )


def require_backend(backend: Backend | None) -> Backend:
    if backend is None:
        raise RuntimeError("Tool instance is not bound to a backend.")
    return backend


run_bash_tool = RunBashTool()
