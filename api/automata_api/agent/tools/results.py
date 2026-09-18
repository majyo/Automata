"""Tool result builders.

Every builtin tool reports failures as a ``ToolResult`` whose content is a
JSON payload the model reads. These builders keep those payloads consistent
across files, patches, searches and process sessions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from automata_api.agent.execution.processes import search_exit_code_is_ok
from automata_api.agent.tools.args import json_response
from automata_api.agent.tools.constants import (
    DEFAULT_EXEC_OUTPUT_CHARS,
    OUTPUT_LIMIT,
)
from automata_api.agent.tools.models import ToolResult
from automata_api.agent.tools.text import truncate_content


def search_tool_result(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    pattern: str,
    path: str,
    cwd: str,
    engine: str,
    command: str,
    timeout_seconds: float,
    process_result: dict[str, Any],
    attempts: list[dict[str, Any]],
) -> ToolResult:
    exit_code = process_result["exit_code"]
    ok = search_exit_code_is_ok(exit_code)
    payload = {
        "simulated": False,
        "ok": ok,
        "matched": exit_code == 0,
        "tool": tool_name,
        "engine": engine,
        "pattern": pattern,
        "path": path,
        "cwd": cwd,
        "command": command,
        "timeout_seconds": timeout_seconds,
        "exit_code": exit_code,
        "timed_out": process_result["timed_out"],
        "stdout": process_result["stdout"],
        "stderr": process_result["stderr"],
        "stdout_truncated": process_result["stdout_truncated"],
        "stderr_truncated": process_result["stderr_truncated"],
        "attempts": attempts,
        "error_code": process_result.get("error_code"),
        "sandbox": process_result.get("sandbox"),
    }
    return ToolResult(
        name=tool_name,
        arguments=arguments,
        content=json_response(payload),
        success=ok,
        error_code=process_result.get("error_code"),
        sandbox=process_result.get("sandbox"),
    )


def search_error_result(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    pattern: str,
    path: str,
    cwd: str,
    timeout_seconds: float,
    engine: str | None,
    error: str,
    attempts: list[dict[str, Any]] | None = None,
) -> ToolResult:
    return ToolResult(
        name=tool_name,
        arguments=arguments,
        content=json_response(
            {
                "simulated": False,
                "ok": False,
                "matched": False,
                "tool": tool_name,
                "engine": engine,
                "pattern": pattern,
                "path": path,
                "cwd": cwd,
                "command": "",
                "timeout_seconds": timeout_seconds,
                "exit_code": None,
                "timed_out": False,
                "stdout": "",
                "stderr": error,
                "stdout_truncated": False,
                "stderr_truncated": False,
                "attempts": attempts or [],
            }
        ),
        success=False,
    )


def search_result_was_no_match(result: ToolResult) -> bool:
    try:
        payload = json.loads(result.content)
    except json.JSONDecodeError:
        return False

    return payload.get("ok") is True and payload.get("matched") is False


def patch_error_result(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    dry_run: bool,
    error: str,
    path: str = "",
    syntax: str | None = None,
    error_code: str | None = None,
) -> ToolResult:
    payload = {
        "simulated": False,
        "ok": False,
        "tool": tool_name,
        "dry_run": dry_run,
        "path": path,
        "error": error,
        "error_code": error_code,
    }
    if syntax is not None:
        payload["syntax"] = syntax
    return ToolResult(
        name=tool_name,
        arguments=arguments,
        content=json_response(payload),
        success=False,
        error_code=error_code,
    )


def file_error_result(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    error: str,
    path: Path | None = None,
    error_code: str | None = None,
) -> ToolResult:
    return ToolResult(
        name=tool_name,
        arguments=arguments,
        content=json_response(
            {
                "simulated": False,
                "ok": False,
                "path": str(path) if path else "",
                "absolute_path": str(path) if path else "",
                "encoding": "utf-8",
                "error": error,
                "error_code": error_code,
            }
        ),
        success=False,
        error_code=error_code,
    )


def exec_command_error_result(
    *,
    arguments: dict[str, Any],
    cmd: str,
    shell: str,
    workdir: str,
    cwd: str,
    timeout_seconds: float,
    error: str,
    shell_path: str | None = None,
    duration_seconds: float = 0.0,
    supported_shells: tuple[str, ...] | None = None,
    error_code: str | None = None,
) -> ToolResult:
    output, output_truncated = truncate_content(
        build_exec_output("", error), DEFAULT_EXEC_OUTPUT_CHARS
    )
    payload: dict[str, Any] = {
        "simulated": False,
        "ok": False,
        "tool": "exec_command",
        "cmd": cmd,
        "shell": shell,
        "workdir": workdir,
        "cwd": cwd,
        "shell_path": shell_path,
        "timeout_seconds": timeout_seconds,
        "duration_seconds": duration_seconds,
        "exit_code": None,
        "timed_out": False,
        "stdout": "",
        "stderr": error,
        "output": output,
        "stdout_truncated": False,
        "stderr_truncated": False,
        "output_truncated": output_truncated,
    }
    if supported_shells is not None:
        payload["supported_shells"] = list(supported_shells)
    if error_code is not None:
        payload["error_code"] = error_code
    return ToolResult(
        name="exec_command",
        arguments=arguments,
        content=json_response(payload),
        success=False,
        error_code=error_code,
    )


def build_exec_output(stdout: str, stderr: str) -> str:
    if stdout and stderr:
        return f"{stdout}\n\nstderr:\n{stderr}"
    if stderr:
        return f"stderr:\n{stderr}"
    return stdout


def process_session_error_result(
    name: str,
    arguments: dict[str, Any],
    error_code: str,
    message: str,
) -> ToolResult:
    return ToolResult(
        name=name,
        arguments=arguments,
        content=json_response(
            {
                "simulated": False,
                "ok": False,
                "tool": name,
                "running": False,
                "error_code": error_code,
                "error": message,
            }
        ),
        success=False,
        error_code=error_code,
    )


def bash_error_result(
    *,
    arguments: dict[str, Any],
    command: str,
    cwd: str,
    timeout_seconds: float,
    error: str,
    shell: str | None = None,
    error_code: str | None = None,
) -> ToolResult:
    return ToolResult(
        name="run_bash",
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
                "error_code": error_code,
            }
        ),
        success=False,
        error_code=error_code,
    )


def decode_output(output: bytes) -> str:
    return output.decode("utf-8", errors="replace")


def truncate_output(output: str) -> tuple[str, bool]:
    if len(output) <= OUTPUT_LIMIT:
        return output, False

    return output[:OUTPUT_LIMIT], True

