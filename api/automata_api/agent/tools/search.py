from typing import Any

from automata_api.agent.backends.base import Backend, BackendError
from automata_api.observability import observe_span

from ._core import (
    SEARCH_TIMEOUT_SECONDS,
    ToolResult,
    search_error_result,
    search_tool_result,
    string_argument,
    timeout_argument,
    json_response,
)
from .base import AgentTool


DEFAULT_FILE_LIST_LIMIT = 500
MAX_FILE_LIST_LIMIT = 2_000
MAX_FILE_LIST_RESULT_CHARS = 20_000
MAX_FILE_LIST_GLOBS = 32
MAX_FILE_LIST_GLOB_CHARS = 256
MAX_FILE_LIST_DEPTH = 64
FILE_LIST_ARGUMENT_NAMES = {
    "mode",
    "path",
    "cwd",
    "timeout_seconds",
    "include_globs",
    "exclude_globs",
    "hidden",
    "max_depth",
    "limit",
    "pattern",
}


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
                    "Search file contents or enumerate workspace files. Omit mode "
                    "or use mode=search for text search. Use mode=files without a "
                    "pattern to list files with bounded path, glob, depth, and "
                    "result limits. Prefer this over grep or shell-based find/ls."
                ),
                "parameters": rg_parameters(),
            },
        }

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        mode = arguments.get("mode", "search")
        if mode is None or mode == "":
            mode = "search"
        if not isinstance(mode, str) or mode not in {"search", "files"}:
            return file_list_error_result(
                arguments=arguments,
                mode=mode,
                error="invalid_mode",
                message="mode must be one of search or files.",
                cwd=self.backend.workspace_label if self.backend else "",
            )
        if mode == "files":
            return await run_file_list(self.backend, arguments)
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
                "parameters": text_search_parameters(),
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
    return text_search_parameters()


def text_search_parameters() -> dict[str, Any]:
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


def rg_parameters() -> dict[str, Any]:
    parameters = text_search_parameters()
    properties = dict(parameters["properties"])
    properties.update(
        {
            "mode": {
                "type": "string",
                "enum": ["search", "files"],
                "description": (
                    "Defaults to search. Use files to enumerate workspace files."
                ),
            },
            "pattern": {
                "type": "string",
                "description": (
                    "Required in search mode and forbidden in files mode."
                ),
            },
            "include_globs": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": MAX_FILE_LIST_GLOBS,
                "description": (
                    "Files mode only. Include paths matching any glob."
                ),
            },
            "exclude_globs": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": MAX_FILE_LIST_GLOBS,
                "description": (
                    "Files mode only. Exclude paths matching any glob."
                ),
            },
            "hidden": {
                "type": "boolean",
                "description": (
                    "Files mode only. Include hidden paths. Defaults to false."
                ),
            },
            "max_depth": {
                "type": "integer",
                "minimum": 0,
                "maximum": MAX_FILE_LIST_DEPTH,
                "description": (
                    "Files mode only. Traversal depth relative to path."
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_FILE_LIST_LIMIT,
                "description": (
                    "Files mode only. Defaults to 500 and is capped at 2000."
                ),
            },
        }
    )
    return {
        "type": "object",
        "properties": properties,
    }


async def run_file_list(
    backend: Backend | None,
    arguments: dict[str, Any],
) -> ToolResult:
    if backend is None:
        raise RuntimeError("Tool instance is not bound to a backend.")

    invalid_names = sorted(set(arguments) - FILE_LIST_ARGUMENT_NAMES)
    if invalid_names:
        return file_list_error_result(
            arguments=arguments,
            error="unsupported_argument",
            message=f"Unsupported files mode argument: {invalid_names[0]}",
            cwd=backend.workspace_label,
        )

    pattern = arguments.get("pattern")
    if pattern is not None and (not isinstance(pattern, str) or pattern.strip()):
        return file_list_error_result(
            arguments=arguments,
            error="pattern_not_allowed_in_files_mode",
            message="pattern is not allowed when mode=files.",
            cwd=backend.workspace_label,
        )

    for name in ("path", "cwd"):
        value = arguments.get(name)
        if value is not None and not isinstance(value, str):
            return file_list_error_result(
                arguments=arguments,
                error="invalid_argument",
                message=f"{name} must be a string.",
                cwd=backend.workspace_label,
            )

    include_globs, error = parse_globs(arguments, "include_globs")
    if error:
        return file_list_error_result(
            arguments=arguments,
            error="invalid_glob",
            message=error,
            cwd=backend.workspace_label,
        )
    exclude_globs, error = parse_globs(arguments, "exclude_globs")
    if error:
        return file_list_error_result(
            arguments=arguments,
            error="invalid_glob",
            message=error,
            cwd=backend.workspace_label,
        )

    hidden = arguments.get("hidden", False)
    if not isinstance(hidden, bool):
        return file_list_error_result(
            arguments=arguments,
            error="invalid_argument",
            message="hidden must be a boolean.",
            cwd=backend.workspace_label,
        )

    max_depth = arguments.get("max_depth")
    if (
        max_depth is not None
        and (
            isinstance(max_depth, bool)
            or not isinstance(max_depth, int)
            or max_depth < 0
            or max_depth > MAX_FILE_LIST_DEPTH
        )
    ):
        return file_list_error_result(
            arguments=arguments,
            error="invalid_max_depth",
            message=f"max_depth must be between 0 and {MAX_FILE_LIST_DEPTH}.",
            cwd=backend.workspace_label,
        )

    limit = arguments.get("limit", DEFAULT_FILE_LIST_LIMIT)
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit < 1
        or limit > MAX_FILE_LIST_LIMIT
    ):
        return file_list_error_result(
            arguments=arguments,
            error="invalid_limit",
            message=f"limit must be between 1 and {MAX_FILE_LIST_LIMIT}.",
            cwd=backend.workspace_label,
        )

    timeout_seconds = timeout_argument(
        {"timeout_seconds": arguments.get("timeout_seconds", SEARCH_TIMEOUT_SECONDS)}
    )
    try:
        async with observe_span(
            "rg.files",
            attributes={
                "include_glob_count": len(include_globs),
                "exclude_glob_count": len(exclude_globs),
                "max_depth": max_depth,
                "limit": limit,
            },
        ) as list_span:
            result = await backend.list_files(
                path=arguments.get("path"),
                cwd=arguments.get("cwd"),
                include_globs=include_globs,
                exclude_globs=exclude_globs,
                hidden=hidden,
                max_depth=max_depth,
                limit=limit,
                max_result_chars=MAX_FILE_LIST_RESULT_CHARS,
                timeout_seconds=timeout_seconds,
            )
            list_span.set_attributes(
                engine=result.engine,
                file_count=len(result.files),
                truncated=result.truncated,
                degraded=result.degraded,
            )
    except BackendError as error:
        message = str(error)
        return file_list_error_result(
            arguments=arguments,
            error=file_list_backend_error_code(message),
            message=message,
            cwd=error.cwd or backend.workspace_label,
        )

    payload = {
        "simulated": False,
        "ok": result.ok,
        "tool": "rg",
        "mode": "files",
        "engine": result.engine,
        "path": result.path,
        "cwd": result.cwd,
        "files": list(result.files),
        "count": len(result.files),
        "truncated": result.truncated,
        "truncation_reason": result.truncation_reason,
        "limit": limit,
        "max_result_chars": MAX_FILE_LIST_RESULT_CHARS,
        "ignore_semantics": result.ignore_semantics,
        "degraded": result.degraded,
        "timed_out": result.timed_out,
        "attempts": result.attempts,
    }
    if result.truncated:
        payload["hint"] = (
            "Narrow path or include_globs and call rg again."
        )
    content = bounded_file_list_content(payload)
    return ToolResult(
        name="rg",
        arguments=arguments,
        content=content,
        success=result.ok,
    )


def parse_globs(
    arguments: dict[str, Any], name: str
) -> tuple[tuple[str, ...], str | None]:
    value = arguments.get(name, [])
    if value is None:
        return (), None
    if not isinstance(value, list):
        return (), f"{name} must be an array of strings."
    if len(value) > MAX_FILE_LIST_GLOBS:
        return (), f"{name} must contain at most {MAX_FILE_LIST_GLOBS} items."

    parsed: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return (), f"{name} entries must be non-empty strings."
        if len(item) > MAX_FILE_LIST_GLOB_CHARS:
            return (), (
                f"{name} entries must be at most "
                f"{MAX_FILE_LIST_GLOB_CHARS} characters."
            )
        if "\x00" in item:
            return (), f"{name} entries must not contain NUL."
        if item.startswith("!"):
            return (), (
                f"{name} entries must not start with '!'; use "
                "exclude_globs for exclusions."
            )
        parsed.append(item)
    return tuple(parsed), None


def file_list_backend_error_code(message: str) -> str:
    lowered = message.lower()
    if "stay inside workspace" in lowered:
        return "path_outside_workspace"
    if "does not exist" in lowered:
        return "path_not_found"
    if "not a directory" in lowered:
        return "path_not_directory"
    if "timed out" in lowered:
        return "enumeration_timed_out"
    return "enumeration_failed"


def bounded_file_list_content(payload: dict[str, Any]) -> str:
    content = json_response(payload)
    files = payload.get("files")
    if not isinstance(files, list):
        return content

    while files and len(content) > MAX_FILE_LIST_RESULT_CHARS:
        files.pop()
        payload["count"] = len(files)
        payload["truncated"] = True
        payload["truncation_reason"] = "character_limit"
        payload["hint"] = (
            "Narrow path or include_globs and call rg again."
        )
        content = json_response(payload)
    return content


def file_list_error_result(
    *,
    arguments: dict[str, Any],
    error: str,
    message: str,
    cwd: str,
    mode: Any = "files",
) -> ToolResult:
    return ToolResult(
        name="rg",
        arguments=arguments,
        content=json_response(
            {
                "simulated": False,
                "ok": False,
                "tool": "rg",
                "mode": mode,
                "error": error,
                "message": message,
                "path": string_argument(arguments, "path", "."),
                "cwd": cwd,
                "files": [],
                "count": 0,
                "truncated": False,
            }
        ),
        success=False,
    )


rg_tool = RgTool()
grep_tool = GrepTool()
