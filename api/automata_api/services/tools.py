import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PlaceholderToolResult:
    name: str
    arguments: dict[str, Any]
    content: str
    success: bool


def placeholder_tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "inspect_workspace",
                "description": (
                    "Inspect the local workspace. Placeholder only: returns a "
                    "simulated workspace summary without reading files."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "focus": {
                            "type": "string",
                            "description": "Optional area to focus the simulated inspection on.",
                        }
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_code",
                "description": (
                    "Search source code for a query. Placeholder only: returns "
                    "simulated matches."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search text or symbol name.",
                        },
                        "path": {
                            "type": "string",
                            "description": "Optional simulated path scope.",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": (
                    "Read a project file. Placeholder only: returns simulated "
                    "file contents and metadata."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Project-relative path to read.",
                        }
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "apply_patch_preview",
                "description": (
                    "Preview a code edit. Placeholder only: returns a simulated "
                    "patch result and does not modify files."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Project-relative path that would be changed.",
                        },
                        "summary": {
                            "type": "string",
                            "description": "Brief description of the intended change.",
                        },
                    },
                    "required": ["path", "summary"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_tests",
                "description": (
                    "Run tests or checks. Placeholder only: returns simulated "
                    "test output."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Test command to simulate.",
                        }
                    },
                    "required": ["command"],
                },
            },
        },
    ]


def run_placeholder_tool(
    name: str, raw_arguments: str | dict[str, Any] | None, workspace: str
) -> PlaceholderToolResult:
    arguments, parse_error = parse_tool_arguments(raw_arguments)
    if parse_error:
        return PlaceholderToolResult(
            name=name,
            arguments={},
            content=json_response(
                {
                    "simulated": True,
                    "tool": name,
                    "ok": False,
                    "error": parse_error,
                }
            ),
            success=False,
        )

    handlers = {
        "inspect_workspace": inspect_workspace,
        "search_code": search_code,
        "read_file": read_file,
        "apply_patch_preview": apply_patch_preview,
        "run_tests": run_tests,
    }
    handler = handlers.get(name)
    if handler is None:
        return PlaceholderToolResult(
            name=name,
            arguments=arguments,
            content=json_response(
                {
                    "simulated": True,
                    "tool": name,
                    "ok": False,
                    "error": f"Unknown placeholder tool: {name}",
                }
            ),
            success=False,
        )

    return PlaceholderToolResult(
        name=name,
        arguments=arguments,
        content=json_response(handler(arguments, workspace)),
        success=True,
    )


def parse_tool_arguments(
    raw_arguments: str | dict[str, Any] | None,
) -> tuple[dict[str, Any], str | None]:
    if raw_arguments is None or raw_arguments == "":
        return {}, None

    if isinstance(raw_arguments, dict):
        return raw_arguments, None

    try:
        parsed = json.loads(raw_arguments)
    except json.JSONDecodeError as error:
        return {}, f"Invalid JSON arguments: {error.msg}"

    if not isinstance(parsed, dict):
        return {}, "Tool arguments must be a JSON object."

    return parsed, None


def inspect_workspace(arguments: dict[str, Any], workspace: str) -> dict[str, Any]:
    focus = string_argument(arguments, "focus", "repository")
    return {
        "simulated": True,
        "ok": True,
        "workspace": workspace,
        "focus": focus,
        "summary": (
            "Placeholder inspection found api/, ui/, and shared project scripts. "
            "No filesystem access was performed."
        ),
        "suggested_next_tools": ["search_code", "read_file", "run_tests"],
    }


def search_code(arguments: dict[str, Any], workspace: str) -> dict[str, Any]:
    query = string_argument(arguments, "query", "")
    path = string_argument(arguments, "path", ".")
    return {
        "simulated": True,
        "ok": True,
        "workspace": workspace,
        "query": query,
        "path": path,
        "matches": [
            {
                "path": f"{path.rstrip('/')}/placeholder.py",
                "line": 12,
                "preview": f"Simulated match for {query or 'empty query'}",
            }
        ],
    }


def read_file(arguments: dict[str, Any], workspace: str) -> dict[str, Any]:
    path = string_argument(arguments, "path", "unknown")
    return {
        "simulated": True,
        "ok": True,
        "workspace": workspace,
        "path": path,
        "content": (
            "This is simulated file content from a placeholder tool. "
            "Use a real file tool before claiming exact code details."
        ),
    }


def apply_patch_preview(arguments: dict[str, Any], workspace: str) -> dict[str, Any]:
    path = string_argument(arguments, "path", "unknown")
    summary = string_argument(arguments, "summary", "No summary provided.")
    return {
        "simulated": True,
        "ok": True,
        "workspace": workspace,
        "path": path,
        "summary": summary,
        "result": "Patch preview accepted. No files were modified.",
    }


def run_tests(arguments: dict[str, Any], workspace: str) -> dict[str, Any]:
    command = string_argument(arguments, "command", "pytest")
    return {
        "simulated": True,
        "ok": True,
        "workspace": workspace,
        "command": command,
        "exit_code": 0,
        "stdout": "Simulated test run passed.",
        "stderr": "",
    }


def string_argument(
    arguments: dict[str, Any], name: str, default: str
) -> str:
    value = arguments.get(name)
    return value if isinstance(value, str) and value.strip() else default


def json_response(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True)
