from typing import Any

from ._core import ToolResult, run_read_file, run_write_file
from .base import AgentTool


class ReadFileTool(AgentTool):
    name = "read_file"

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Read a real UTF-8 text file from the workspace. The path "
                    "must stay inside the workspace."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Project-relative path to read.",
                        },
                        "start_line": {
                            "type": "integer",
                            "description": "Optional 1-based first line to return.",
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "Optional 1-based last line to return.",
                        },
                    },
                    "required": ["path"],
                },
            },
        }

    async def run(self, arguments: dict[str, Any], workspace: str) -> ToolResult:
        return run_read_file(arguments, workspace)


class WriteFileTool(AgentTool):
    name = "write_file"

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Write UTF-8 text to a real file in the workspace. The path "
                    "must stay inside the workspace. Use only when the user has "
                    "asked for file changes."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Project-relative path to write.",
                        },
                        "content": {
                            "type": "string",
                            "description": "Complete text content to write or append.",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["overwrite", "create", "append"],
                            "description": (
                                "Write mode. overwrite replaces or creates, create "
                                "fails if the file exists, append appends to the file."
                            ),
                        },
                        "create_dirs": {
                            "type": "boolean",
                            "description": "Create parent directories when missing. Defaults to true.",
                        },
                    },
                    "required": ["path", "content"],
                },
            },
        }

    async def run(self, arguments: dict[str, Any], workspace: str) -> ToolResult:
        return run_write_file(arguments, workspace)


read_file_tool = ReadFileTool()
write_file_tool = WriteFileTool()
