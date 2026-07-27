from typing import Any

from automata_api.agent.backends.base import Backend, BackendError

from ._core import (
    ToolResult,
    bool_argument,
    file_error_result,
    json_response,
    select_line_range,
    string_argument,
    truncate_content,
)
from .base import AgentTool


class ReadFileTool(AgentTool):
    name = "read_file"
    read_only = True

    def __init__(self, backend: Backend | None = None) -> None:
        self.backend = backend

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

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        backend = require_backend(self.backend)
        path = arguments.get("path")
        if not isinstance(path, str) or not path.strip():
            return file_error_result(
                "read_file",
                arguments,
                error="Missing required string path.",
            )
        try:
            stat = await backend.stat(path)
        except BackendError as error:
            return file_error_result(
                "read_file",
                arguments,
                error=str(error),
                error_code=error.error_code,
            )

        if not stat.exists:
            return file_error_result_for_stat(
                "read_file",
                arguments,
                stat,
                error=f"File does not exist: {stat.absolute_path}",
            )
        if not stat.is_file:
            return file_error_result_for_stat(
                "read_file",
                arguments,
                stat,
                error=f"Path is not a file: {stat.absolute_path}",
            )

        try:
            raw_content = await backend.read_file(stat.path, errors="replace")
        except BackendError as error:
            return file_error_result(
                "read_file",
                arguments,
                error=f"Failed to read file: {error}",
                error_code=error.error_code,
            )

        content, start_line, end_line, total_lines = select_line_range(
            raw_content,
            arguments.get("start_line"),
            arguments.get("end_line"),
        )
        content, truncated = truncate_content(content, 120_000)
        payload = {
            "simulated": False,
            "ok": True,
            "path": stat.path,
            "absolute_path": stat.absolute_path,
            "encoding": "utf-8",
            "size_bytes": len(raw_content.encode("utf-8")),
            "content": content,
            "truncated": truncated,
            "start_line": start_line,
            "end_line": end_line,
            "total_lines": total_lines,
        }
        return ToolResult(
            name="read_file",
            arguments=arguments,
            content=json_response(payload),
            success=True,
        )


class WriteFileTool(AgentTool):
    name = "write_file"

    def __init__(self, backend: Backend | None = None) -> None:
        self.backend = backend

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

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        backend = require_backend(self.backend)
        path = arguments.get("path")
        if not isinstance(path, str) or not path.strip():
            return file_error_result(
                "write_file",
                arguments,
                error="Missing required string path.",
            )
        try:
            before = await backend.stat(path)
        except BackendError as error:
            return file_error_result(
                "write_file",
                arguments,
                error=str(error),
                error_code=error.error_code,
            )

        content = arguments.get("content")
        if not isinstance(content, str):
            return file_error_result_for_stat(
                "write_file",
                arguments,
                before,
                error="Missing required string content.",
            )

        mode = string_argument(arguments, "mode", "overwrite")
        if mode not in {"overwrite", "create", "append"}:
            return file_error_result_for_stat(
                "write_file",
                arguments,
                before,
                error="mode must be one of overwrite, create, or append.",
            )
        if before.exists and before.is_dir:
            return file_error_result_for_stat(
                "write_file",
                arguments,
                before,
                error=f"Path is a directory: {before.absolute_path}",
            )
        if mode == "create" and before.exists:
            return file_error_result_for_stat(
                "write_file",
                arguments,
                before,
                error=f"File already exists: {before.absolute_path}",
            )

        create_dirs = bool_argument(arguments, "create_dirs", True)
        try:
            bytes_written = await backend.write_file(
                before.path,
                content,
                mode=mode,
                create_dirs=create_dirs,
            )
            after = await backend.stat(before.path)
        except BackendError as error:
            return file_error_result(
                "write_file",
                arguments,
                error=str(error),
                error_code=error.error_code,
            )

        payload = {
            "simulated": False,
            "ok": True,
            "path": after.path,
            "absolute_path": after.absolute_path,
            "encoding": "utf-8",
            "mode": mode,
            "existed_before": before.exists,
            "bytes_written": bytes_written,
            "size_bytes": after.size_bytes,
        }
        return ToolResult(
            name="write_file",
            arguments=arguments,
            content=json_response(payload),
            success=True,
        )


def require_backend(backend: Backend | None) -> Backend:
    if backend is None:
        raise RuntimeError("Tool instance is not bound to a backend.")
    return backend


def file_error_result_for_stat(
    tool_name: str,
    arguments: dict[str, Any],
    stat,
    *,
    error: str,
    error_code: str | None = None,
) -> ToolResult:
    return ToolResult(
        name=tool_name,
        arguments=arguments,
        content=json_response(
            {
                "simulated": False,
                "ok": False,
                "path": stat.path,
                "absolute_path": stat.absolute_path,
                "encoding": "utf-8",
                "error": error,
                "error_code": error_code,
            }
        ),
        success=False,
        error_code=error_code,
    )


read_file_tool = ReadFileTool()
write_file_tool = WriteFileTool()
