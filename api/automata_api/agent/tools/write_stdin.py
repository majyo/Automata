from typing import Any

from automata_api.agent.backends.base import Backend

from ._core import ToolResult, run_write_stdin
from .base import AgentTool


class WriteStdinTool(AgentTool):
    name = "write_stdin"

    def __init__(self, backend: Backend | None = None) -> None:
        self.backend = backend

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Poll a running exec_command process session or write UTF-8 "
                    "characters to its stdin pipe. This is non-PTY interaction: "
                    "terminal line editing, password prompts, resize, and full-screen "
                    "TUI behavior are not supported."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": (
                                "Process session id returned by exec_command."
                            ),
                        },
                        "chars": {
                            "type": "string",
                            "description": (
                                "UTF-8 characters to write. Use an empty string "
                                "to poll without writing."
                            ),
                        },
                        "yield_time_ms": {
                            "type": "integer",
                            "description": (
                                "How long to wait for output or process exit. "
                                "Defaults to 250 ms and is capped at 30000 ms."
                            ),
                        },
                        "max_output_chars": {
                            "type": "integer",
                            "description": (
                                "Character limit for this response. Defaults to "
                                "20000 and is capped at 60000."
                            ),
                        },
                    },
                    "required": ["session_id"],
                },
            },
        }

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        if self.backend is None:
            raise RuntimeError("Tool instance is not bound to a backend.")
        return await run_write_stdin(arguments, self.backend.workspace_label)


write_stdin_tool = WriteStdinTool()
