from typing import Any

from automata_api.agent.backends.base import Backend, BackendError

from ._core import (
    SEARCH_TIMEOUT_SECONDS,
    ToolResult,
    search_error_result,
    search_tool_result,
    string_argument,
    timeout_argument,
)
from .base import AgentTool


class RgTool(AgentTool):
    name = "rg"
    read_only = True

    def __init__(self, backend: Backend | None = None) -> None:
        self.backend = backend

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

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        return await run_search(self.backend, self.name, arguments, prefer="rg")


class GrepTool(AgentTool):
    name = "grep"
    read_only = True

    def __init__(self, backend: Backend | None = None) -> None:
        self.backend = backend

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

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        return await run_search(self.backend, self.name, arguments, prefer="grep")


async def run_search(
    backend: Backend | None,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    prefer: str,
) -> ToolResult:
    if backend is None:
        raise RuntimeError("Tool instance is not bound to a backend.")

    pattern = string_argument(arguments, "pattern", "")
    timeout_seconds = timeout_argument(
        {"timeout_seconds": arguments.get("timeout_seconds", SEARCH_TIMEOUT_SECONDS)}
    )
    requested_path = string_argument(arguments, "path", ".")
    if not pattern:
        return search_error_result(
            tool_name=tool_name,
            arguments=arguments,
            pattern=pattern,
            path=requested_path,
            cwd=backend.workspace_label,
            timeout_seconds=timeout_seconds,
            engine=None,
            error="Missing required pattern.",
        )

    try:
        result = await backend.search(
            pattern,
            path=arguments.get("path"),
            cwd=arguments.get("cwd"),
            timeout_seconds=timeout_seconds,
            prefer=prefer,
        )
    except BackendError as error:
        return search_error_result(
            tool_name=tool_name,
            arguments=arguments,
            pattern=pattern,
            path=requested_path,
            cwd=error.cwd or backend.workspace_label,
            timeout_seconds=timeout_seconds,
            engine=None,
            error=str(error),
        )

    return search_tool_result(
        tool_name=tool_name,
        arguments=arguments,
        pattern=result.pattern,
        path=result.path,
        cwd=result.cwd,
        engine=result.engine,
        command=result.command,
        timeout_seconds=result.timeout_seconds,
        process_result={
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "stdout_truncated": result.stdout_truncated,
            "stderr_truncated": result.stderr_truncated,
        },
        attempts=result.attempts,
    )


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
