import asyncio
import json
import os
import shutil
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_BASH_TIMEOUT_SECONDS = 30.0
MAX_BASH_TIMEOUT_SECONDS = 120.0
OUTPUT_LIMIT = 20_000
SEARCH_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class ToolResult:
    name: str
    arguments: dict[str, Any]
    content: str
    success: bool


PlaceholderToolResult = ToolResult


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
                "name": "rg",
                "description": (
                    "Search files in the workspace. Prefer this over grep. It "
                    "tries ripgrep first, falls back to grep, then falls back to "
                    "run_bash with a suitable search command for the environment."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Search pattern.",
                        },
                        "path": {
                            "type": "string",
                            "description": (
                                "Optional workspace-relative file or directory "
                                "to search. Defaults to the workspace root."
                            ),
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
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "grep",
                "description": (
                    "Search files in the workspace using grep semantics. Use rg "
                    "first unless grep is specifically needed. Falls back to "
                    "run_bash with grep when native grep is unavailable."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Search pattern.",
                        },
                        "path": {
                            "type": "string",
                            "description": (
                                "Optional workspace-relative file or directory "
                                "to search. Defaults to the workspace root."
                            ),
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
                    "required": ["pattern"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_bash",
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


async def run_tool(
    name: str, raw_arguments: str | dict[str, Any] | None, workspace: str
) -> ToolResult:
    arguments, parse_error = parse_tool_arguments(raw_arguments)
    if parse_error:
        return ToolResult(
            name=name,
            arguments={},
            content=json_response(
                {
                    "simulated": not is_real_tool(name),
                    "tool": name,
                    "ok": False,
                    "error": parse_error,
                }
            ),
            success=False,
        )

    if name == "run_bash":
        return await run_bash(arguments, workspace)
    if name == "rg":
        return await run_rg(arguments, workspace)
    if name == "grep":
        return await run_grep(arguments, workspace)

    handlers = {
        "inspect_workspace": inspect_workspace,
        "search_code": search_code,
        "read_file": read_file,
        "apply_patch_preview": apply_patch_preview,
        "run_tests": run_tests,
    }
    handler = handlers.get(name)
    if handler is None:
        return ToolResult(
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

    return ToolResult(
        name=name,
        arguments=arguments,
        content=json_response(handler(arguments, workspace)),
        success=True,
    )


def is_real_tool(name: str) -> bool:
    return name in {"run_bash", "rg", "grep"}


def run_placeholder_tool(
    name: str, raw_arguments: str | dict[str, Any] | None, workspace: str
) -> ToolResult:
    arguments, parse_error = parse_tool_arguments(raw_arguments)
    if parse_error:
        return ToolResult(
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
        return ToolResult(
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

    return ToolResult(
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


async def run_rg(arguments: dict[str, Any], workspace: str) -> ToolResult:
    return await run_search_tool(
        tool_name="rg",
        arguments=arguments,
        workspace=workspace,
        engines=("rg", "grep", "bash"),
    )


async def run_grep(arguments: dict[str, Any], workspace: str) -> ToolResult:
    return await run_search_tool(
        tool_name="grep",
        arguments=arguments,
        workspace=workspace,
        engines=("grep", "bash"),
    )


async def run_search_tool(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    workspace: str,
    engines: tuple[str, ...],
) -> ToolResult:
    pattern = string_argument(arguments, "pattern", "")
    timeout_seconds = timeout_argument(
        {"timeout_seconds": arguments.get("timeout_seconds", SEARCH_TIMEOUT_SECONDS)}
    )
    workspace_path = Path(workspace).expanduser().resolve()
    cwd_result = resolve_tool_cwd(workspace_path, arguments.get("cwd"))

    if not pattern:
        return search_error_result(
            tool_name=tool_name,
            arguments=arguments,
            pattern=pattern,
            path=string_argument(arguments, "path", "."),
            cwd=str(workspace_path),
            timeout_seconds=timeout_seconds,
            engine=None,
            error="Missing required pattern.",
        )

    if isinstance(cwd_result, str):
        return search_error_result(
            tool_name=tool_name,
            arguments=arguments,
            pattern=pattern,
            path=string_argument(arguments, "path", "."),
            cwd=str(workspace_path),
            timeout_seconds=timeout_seconds,
            engine=None,
            error=cwd_result,
        )

    search_path_result = resolve_search_path(
        workspace_path=workspace_path,
        cwd_path=cwd_result,
        raw_path=arguments.get("path"),
    )
    if isinstance(search_path_result, str):
        return search_error_result(
            tool_name=tool_name,
            arguments=arguments,
            pattern=pattern,
            path=string_argument(arguments, "path", "."),
            cwd=str(cwd_result),
            timeout_seconds=timeout_seconds,
            engine=None,
            error=search_path_result,
        )

    attempts: list[dict[str, Any]] = []
    for engine in engines:
        if engine == "bash":
            result = await run_bash_search(
                tool_name=tool_name,
                arguments=arguments,
                pattern=pattern,
                search_path=search_path_result,
                workspace_path=workspace_path,
                cwd_path=cwd_result,
                timeout_seconds=timeout_seconds,
                preferred_engine="rg" if tool_name == "rg" else "grep",
                attempts=attempts,
            )
            return result

        executable = resolve_executable(engine)
        if executable is None:
            attempts.append({"engine": engine, "ok": False, "error": "not found"})
            continue

        result = await run_native_search(
            tool_name=tool_name,
            arguments=arguments,
            engine=engine,
            executable=executable,
            pattern=pattern,
            search_path=search_path_result,
            cwd_path=cwd_result,
            timeout_seconds=timeout_seconds,
            attempts=attempts,
        )
        if result.success or search_result_was_no_match(result):
            return result

    return search_error_result(
        tool_name=tool_name,
        arguments=arguments,
        pattern=pattern,
        path=string_argument(arguments, "path", "."),
        cwd=str(cwd_result),
        timeout_seconds=timeout_seconds,
        engine=None,
        error="Could not find a usable search command.",
        attempts=attempts,
    )


async def run_native_search(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    engine: str,
    executable: str,
    pattern: str,
    search_path: Path,
    cwd_path: Path,
    timeout_seconds: float,
    attempts: list[dict[str, Any]],
) -> ToolResult:
    relative_path = path_argument_for_cwd(search_path, cwd_path)
    if engine == "rg":
        command = [executable, "--line-number", "--color", "never", "--", pattern, relative_path]
    else:
        command = [executable, "-R", "-n", "--", pattern, relative_path]

    process_result = await run_process(command, cwd_path, timeout_seconds)
    attempts.append(
        {
            "engine": engine,
            "ok": search_exit_code_is_ok(process_result["exit_code"]),
            "exit_code": process_result["exit_code"],
            "timed_out": process_result["timed_out"],
        }
    )
    return search_tool_result(
        tool_name=tool_name,
        arguments=arguments,
        pattern=pattern,
        path=relative_path,
        cwd=str(cwd_path),
        engine=engine,
        command=display_command(command),
        timeout_seconds=timeout_seconds,
        process_result=process_result,
        attempts=attempts,
    )


async def run_bash_search(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    pattern: str,
    search_path: Path,
    workspace_path: Path,
    cwd_path: Path,
    timeout_seconds: float,
    preferred_engine: str,
    attempts: list[dict[str, Any]],
) -> ToolResult:
    relative_path = path_argument_for_cwd(search_path, cwd_path)
    command = bash_search_command(preferred_engine, pattern, relative_path)
    bash_result = await run_bash(
        {
            "command": command,
            "cwd": path_argument_for_cwd(cwd_path, workspace_path),
            "timeout_seconds": timeout_seconds,
        },
        str(workspace_path),
    )
    payload = json.loads(bash_result.content)
    process_result = {
        "exit_code": payload["exit_code"],
        "timed_out": payload["timed_out"],
        "stdout": payload["stdout"],
        "stderr": payload["stderr"],
        "stdout_truncated": payload["stdout_truncated"],
        "stderr_truncated": payload["stderr_truncated"],
    }
    attempts.append(
        {
            "engine": "bash",
            "ok": search_exit_code_is_ok(process_result["exit_code"]),
            "exit_code": process_result["exit_code"],
            "timed_out": process_result["timed_out"],
        }
    )
    return search_tool_result(
        tool_name=tool_name,
        arguments=arguments,
        pattern=pattern,
        path=relative_path,
        cwd=str(cwd_path),
        engine="bash",
        command=command,
        timeout_seconds=timeout_seconds,
        process_result=process_result,
        attempts=attempts,
    )


async def run_process(
    command: list[str], cwd_path: Path, timeout_seconds: float
) -> dict[str, Any]:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        return {
            "exit_code": None,
            "timed_out": False,
            "stdout": "",
            "stderr": f"Failed to start process: {error}",
            "stdout_truncated": False,
            "stderr_truncated": False,
        }

    timed_out = False
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
        exit_code = process.returncode
    except TimeoutError:
        timed_out = True
        process.kill()
        stdout_bytes, stderr_bytes = await process.communicate()
        exit_code = None

    stdout, stdout_truncated = truncate_output(decode_output(stdout_bytes))
    stderr, stderr_truncated = truncate_output(decode_output(stderr_bytes))
    return {
        "exit_code": exit_code,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }


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
    }
    return ToolResult(
        name=tool_name,
        arguments=arguments,
        content=json_response(payload),
        success=ok,
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


def resolve_executable(name: str) -> str | None:
    return shutil.which(name)


def resolve_search_path(
    *, workspace_path: Path, cwd_path: Path, raw_path: Any
) -> Path | str:
    requested_path = raw_path if isinstance(raw_path, str) and raw_path.strip() else "."
    path = Path(requested_path).expanduser()
    if path.is_absolute():
        resolved = path.resolve()
    else:
        resolved = (cwd_path / path).resolve()

    try:
        resolved.relative_to(workspace_path)
    except ValueError:
        return f"path must stay inside workspace: {workspace_path}"

    if not resolved.exists():
        return f"path does not exist: {resolved}"

    return resolved


def path_argument_for_cwd(path: Path, cwd_path: Path) -> str:
    relative = os.path.relpath(path, cwd_path)
    return Path(relative).as_posix()


def bash_search_command(preferred_engine: str, pattern: str, path: str) -> str:
    quoted_pattern = shlex.quote(pattern)
    quoted_path = shlex.quote(path)
    if preferred_engine == "rg":
        return (
            "if command -v rg >/dev/null 2>&1; then "
            f"rg --line-number --color never -- {quoted_pattern} {quoted_path}; "
            "status=$?; if [ \"$status\" -le 1 ]; then exit \"$status\"; fi; "
            "fi; "
            "if command -v grep >/dev/null 2>&1; then "
            f"grep -R -n -- {quoted_pattern} {quoted_path}; "
            "exit $?; "
            "fi; "
            "echo 'Could not find rg or grep.' >&2; exit 127"
        )

    return (
        "if command -v grep >/dev/null 2>&1; then "
        f"grep -R -n -- {quoted_pattern} {quoted_path}; "
        "exit $?; "
        "fi; "
        "echo 'Could not find grep.' >&2; exit 127"
    )


def display_command(command: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def search_exit_code_is_ok(exit_code: Any) -> bool:
    return exit_code in (0, 1)


def search_result_was_no_match(result: ToolResult) -> bool:
    try:
        payload = json.loads(result.content)
    except json.JSONDecodeError:
        return False

    return payload.get("ok") is True and payload.get("matched") is False


async def run_bash(arguments: dict[str, Any], workspace: str) -> ToolResult:
    command = string_argument(arguments, "command", "")
    timeout_seconds = timeout_argument(arguments)
    workspace_path = Path(workspace).expanduser().resolve()
    cwd_result = resolve_tool_cwd(workspace_path, arguments.get("cwd"))

    if not command:
        return bash_error_result(
            arguments=arguments,
            command=command,
            cwd=str(workspace_path),
            timeout_seconds=timeout_seconds,
            error="Missing required command.",
        )

    if isinstance(cwd_result, str):
        return bash_error_result(
            arguments=arguments,
            command=command,
            cwd=str(workspace_path),
            timeout_seconds=timeout_seconds,
            error=cwd_result,
        )

    bash_path = resolve_bash_executable()
    if bash_path is None:
        return bash_error_result(
            arguments=arguments,
            command=command,
            cwd=str(cwd_result),
            timeout_seconds=timeout_seconds,
            error="Could not find bash. Install Git Bash on Windows or bash on PATH.",
        )

    try:
        process = await asyncio.create_subprocess_exec(
            bash_path,
            "-lc",
            command,
            cwd=str(cwd_result),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as error:
        return bash_error_result(
            arguments=arguments,
            command=command,
            cwd=str(cwd_result),
            timeout_seconds=timeout_seconds,
            error=f"Failed to start bash: {error}",
            shell=bash_path,
        )

    timed_out = False
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
        exit_code = process.returncode
    except TimeoutError:
        timed_out = True
        process.kill()
        stdout_bytes, stderr_bytes = await process.communicate()
        exit_code = None

    stdout, stdout_truncated = truncate_output(decode_output(stdout_bytes))
    stderr, stderr_truncated = truncate_output(decode_output(stderr_bytes))
    payload = {
        "simulated": False,
        "ok": exit_code == 0,
        "command": command,
        "cwd": str(cwd_result),
        "shell": bash_path,
        "timeout_seconds": timeout_seconds,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }
    return ToolResult(
        name="run_bash",
        arguments=arguments,
        content=json_response(payload),
        success=payload["ok"],
    )


def resolve_bash_executable() -> str | None:
    if os.name == "nt":
        candidates = (
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
            shutil.which("bash"),
        )
        for candidate in candidates:
            if candidate and Path(candidate).is_file() and not is_wsl_bash(candidate):
                return candidate
        return None

    path_bash = shutil.which("bash")
    if path_bash:
        return path_bash

    return None


def is_wsl_bash(path: str) -> bool:
    normalized = str(Path(path)).lower()
    return (
        "\\windows\\system32\\bash.exe" in normalized
        or "\\windows\\sysnative\\bash.exe" in normalized
    )


def resolve_tool_cwd(workspace_path: Path, raw_cwd: Any) -> Path | str:
    requested_cwd = raw_cwd if isinstance(raw_cwd, str) and raw_cwd.strip() else "."
    cwd_path = Path(requested_cwd).expanduser()
    if cwd_path.is_absolute():
        resolved = cwd_path.resolve()
    else:
        resolved = (workspace_path / cwd_path).resolve()

    try:
        resolved.relative_to(workspace_path)
    except ValueError:
        return f"cwd must stay inside workspace: {workspace_path}"

    if not resolved.exists():
        return f"cwd does not exist: {resolved}"

    if not resolved.is_dir():
        return f"cwd is not a directory: {resolved}"

    return resolved


def timeout_argument(arguments: dict[str, Any]) -> float:
    raw_value = arguments.get("timeout_seconds", DEFAULT_BASH_TIMEOUT_SECONDS)
    if isinstance(raw_value, int | float):
        timeout_seconds = float(raw_value)
    elif isinstance(raw_value, str):
        try:
            timeout_seconds = float(raw_value)
        except ValueError:
            timeout_seconds = DEFAULT_BASH_TIMEOUT_SECONDS
    else:
        timeout_seconds = DEFAULT_BASH_TIMEOUT_SECONDS

    if timeout_seconds <= 0:
        return DEFAULT_BASH_TIMEOUT_SECONDS

    return min(timeout_seconds, MAX_BASH_TIMEOUT_SECONDS)


def bash_error_result(
    *,
    arguments: dict[str, Any],
    command: str,
    cwd: str,
    timeout_seconds: float,
    error: str,
    shell: str | None = None,
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
            }
        ),
        success=False,
    )


def decode_output(output: bytes) -> str:
    return output.decode("utf-8", errors="replace")


def truncate_output(output: str) -> tuple[str, bool]:
    if len(output) <= OUTPUT_LIMIT:
        return output, False

    return output[:OUTPUT_LIMIT], True


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
