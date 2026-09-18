import asyncio
import json
import os
import re
import shlex
import shutil
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from automata_api.agent.execution.output import (
    CapturedProcessOutput,
    CapturedStream,
    HeadTailTextBuffer,
    append_limited_text,
    capture_process_output,
    read_limited_stream,
)

# Re-exports below are the compatibility facade for callers that still
# reach execution helpers through ``tools._core`` (notably the local
# backend). They are aliased to themselves so linters keep them.
from automata_api.agent.execution.process import (
    process_supervisor as process_supervisor,
)
from automata_api.agent.execution.process_sessions import (
    ProcessSessionError,
    ProcessSessionSnapshot,
)
from automata_api.agent.execution.process_sessions import (
    process_session_manager as process_session_manager,
)
from automata_api.agent.execution.sandbox import process_launcher
from automata_api.agent.execution.sandbox.errors import SandboxError
from automata_api.agent.execution.sandbox.launcher import (
    emit_sandbox_event as emit_sandbox_event,
)
from automata_api.agent.execution.tool_output import (
    emit_tool_output as emit_tool_output,
)
from automata_api.agent.tools.args import (
    DEFAULT_BASH_TIMEOUT_SECONDS,
    DEFAULT_STDIN_YIELD_MILLISECONDS,
    MAX_BASH_TIMEOUT_SECONDS,
    MAX_PROCESS_YIELD_MILLISECONDS,
    bool_argument,
    json_response,
    max_output_chars_argument,
    parse_tool_arguments,
    positive_int_argument,
    string_argument,
    timeout_argument,
    yield_time_ms_argument,
)
from automata_api.agent.tools.constants import (
    DEFAULT_EXEC_OUTPUT_CHARS,
    FILE_READ_LIMIT,
    MAX_EXEC_OUTPUT_CHARS,
    OUTPUT_LIMIT,
    PROCESS_OUTPUT_CHUNK_BYTES,
    SEARCH_TIMEOUT_SECONDS,
    SUPPORTED_EXEC_SHELLS,
)
from automata_api.agent.tools.exec_shell import (
    ExecShellResolution as ExecShellResolution,
)
from automata_api.agent.tools.exec_shell import (
    resolve_exec_shell as resolve_exec_shell,
)
from automata_api.agent.tools.exec_shell import (
    shell_argument as shell_argument,
)
from automata_api.agent.tools.models import ToolResult
from automata_api.agent.tools.results import (
    bash_error_result as bash_error_result,
)
from automata_api.agent.tools.results import (
    build_exec_output as build_exec_output,
)
from automata_api.agent.tools.results import (
    decode_output as decode_output,
)
from automata_api.agent.tools.results import (
    exec_command_error_result as exec_command_error_result,
)
from automata_api.agent.tools.results import (
    file_error_result as file_error_result,
)
from automata_api.agent.tools.results import (
    patch_error_result as patch_error_result,
)
from automata_api.agent.tools.results import (
    process_session_error_result as process_session_error_result,
)
from automata_api.agent.tools.results import (
    search_error_result as search_error_result,
)
from automata_api.agent.tools.results import (
    search_result_was_no_match as search_result_was_no_match,
)
from automata_api.agent.tools.results import (
    search_tool_result as search_tool_result,
)
from automata_api.agent.tools.results import (
    truncate_output as truncate_output,
)
from automata_api.agent.tools.sessions import (
    bound_process_session_snapshot as bound_process_session_snapshot,
)
from automata_api.agent.tools.sessions import (
    emit_process_session_output as emit_process_session_output,
)
from automata_api.agent.tools.sessions import (
    process_session_tool_result as process_session_tool_result,
)
from automata_api.agent.tools.sessions import (
    run_write_stdin as run_write_stdin,
)
from automata_api.agent.tools.text import (
    select_line_range as select_line_range,
)
from automata_api.agent.tools.text import (
    truncate_content as truncate_content,
)
from automata_api.agent.tools.text import (
    truncate_head_tail_content as truncate_head_tail_content,
)
from automata_api.workspace.patches.parsing import (
    PatchFile as PatchFile,
)
from automata_api.workspace.patches.parsing import (
    PatchHunk as PatchHunk,
)
from automata_api.workspace.patches.parsing import (
    PatchHunkLine as PatchHunkLine,
)
from automata_api.workspace.patches.parsing import (
    apply_hunks_to_content as apply_hunks_to_content,
)
from automata_api.workspace.patches.parsing import (
    diff_header_path as diff_header_path,
)
from automata_api.workspace.patches.parsing import (
    parse_patch_hunk as parse_patch_hunk,
)
from automata_api.workspace.patches.parsing import (
    patch_file_status as patch_file_status,
)
from automata_api.workspace.patches.parsing import (
    patch_summary as patch_summary,
)
from automata_api.workspace.paths import (
    path_argument_for_cwd as path_argument_for_cwd,
)
from automata_api.workspace.paths import (
    resolve_executable as resolve_executable,
)
from automata_api.workspace.paths import (
    resolve_file_path as resolve_file_path,
)
from automata_api.workspace.paths import (
    resolve_search_path as resolve_search_path,
)
from automata_api.workspace.paths import (
    resolve_tool_cwd as resolve_tool_cwd,
)

from .patch_codex import (
    CodexPatchFile,
    apply_codex_hunks_to_content,
    parse_codex_patch,
)

# NOTE: no ``__all__`` here on purpose. ``automata_api.agent.tools``
# star-imports this module, so an explicit list would silently stop
# re-exporting every helper that callers and tests reach through the
# package. M7 narrows the surface deliberately, together with callers.


async def run_rg(arguments: dict[str, Any], workspace: str) -> ToolResult:
    from automata_api.agent.backends.local import LocalBackend
    from automata_api.agent.tools.search import RgTool

    return await RgTool(LocalBackend(workspace)).run(arguments)


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
        process = await process_launcher.spawn(
            *command,
            cwd=str(cwd_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            scope_name="tool-search",
        )
    except SandboxError as error:
        return {
            "exit_code": None,
            "timed_out": False,
            "stdout": "",
            "stderr": error.public_message,
            "stdout_truncated": False,
            "stderr_truncated": False,
            "error_code": error.code,
        }
    except OSError as error:
        return {
            "exit_code": None,
            "timed_out": False,
            "stdout": "",
            "stderr": f"Failed to start process: {error}",
            "stdout_truncated": False,
            "stderr_truncated": False,
        }

    output = await capture_process_output(
        process,
        timeout_seconds,
        stdout_limit=OUTPUT_LIMIT,
        stderr_limit=OUTPUT_LIMIT,
    )
    return {
        "exit_code": output.exit_code,
        "timed_out": output.timed_out,
        "stdout": output.stdout.text,
        "stderr": output.stderr.text,
        "stdout_truncated": output.stdout.truncated,
        "stderr_truncated": output.stderr.truncated,
        "error_code": (
            output.sandbox_failure.code
            if output.sandbox_failure is not None
            else None
        ),
        "sandbox": output.sandbox.to_dict() if output.sandbox is not None else None,
    }






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




def run_read_file(arguments: dict[str, Any], workspace: str) -> ToolResult:
    workspace_path = Path(workspace).expanduser().resolve()
    path_result = resolve_file_path(workspace_path, arguments.get("path"))
    if isinstance(path_result, str):
        return file_error_result("read_file", arguments, error=path_result)

    if not path_result.exists():
        return file_error_result(
            "read_file", arguments, path=path_result, error=f"File does not exist: {path_result}"
        )

    if not path_result.is_file():
        return file_error_result(
            "read_file", arguments, path=path_result, error=f"Path is not a file: {path_result}"
        )

    try:
        raw_content = path_result.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        return file_error_result(
            "read_file", arguments, path=path_result, error=f"Failed to read file: {error}"
        )

    content, start_line, end_line, total_lines = select_line_range(
        raw_content,
        arguments.get("start_line"),
        arguments.get("end_line"),
    )
    content, truncated = truncate_content(content, FILE_READ_LIMIT)
    payload = {
        "simulated": False,
        "ok": True,
        "path": path_argument_for_cwd(path_result, workspace_path),
        "absolute_path": str(path_result),
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


def run_write_file(arguments: dict[str, Any], workspace: str) -> ToolResult:
    workspace_path = Path(workspace).expanduser().resolve()
    path_result = resolve_file_path(workspace_path, arguments.get("path"))
    if isinstance(path_result, str):
        return file_error_result("write_file", arguments, error=path_result)

    content = arguments.get("content")
    if not isinstance(content, str):
        return file_error_result(
            "write_file",
            arguments,
            path=path_result,
            error="Missing required string content.",
        )

    mode = string_argument(arguments, "mode", "overwrite")
    if mode not in {"overwrite", "create", "append"}:
        return file_error_result(
            "write_file",
            arguments,
            path=path_result,
            error="mode must be one of overwrite, create, or append.",
        )

    if path_result.exists() and path_result.is_dir():
        return file_error_result(
            "write_file",
            arguments,
            path=path_result,
            error=f"Path is a directory: {path_result}",
        )

    existed_before = path_result.exists()
    if mode == "create" and existed_before:
        return file_error_result(
            "write_file",
            arguments,
            path=path_result,
            error=f"File already exists: {path_result}",
        )

    create_dirs = bool_argument(arguments, "create_dirs", True)
    if not path_result.parent.exists():
        if not create_dirs:
            return file_error_result(
                "write_file",
                arguments,
                path=path_result,
                error=f"Parent directory does not exist: {path_result.parent}",
            )
        try:
            path_result.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            return file_error_result(
                "write_file",
                arguments,
                path=path_result,
                error=f"Failed to create parent directory: {error}",
            )

    try:
        if mode == "append":
            with path_result.open("a", encoding="utf-8", newline="") as file:
                file.write(content)
        elif mode == "create":
            with path_result.open("x", encoding="utf-8", newline="") as file:
                file.write(content)
        else:
            path_result.write_text(content, encoding="utf-8", newline="")
    except OSError as error:
        return file_error_result(
            "write_file", arguments, path=path_result, error=f"Failed to write file: {error}"
        )

    payload = {
        "simulated": False,
        "ok": True,
        "path": path_argument_for_cwd(path_result, workspace_path),
        "absolute_path": str(path_result),
        "encoding": "utf-8",
        "mode": mode,
        "existed_before": existed_before,
        "bytes_written": len(content.encode("utf-8")),
        "size_bytes": path_result.stat().st_size,
    }
    return ToolResult(
        name="write_file",
        arguments=arguments,
        content=json_response(payload),
        success=True,
    )


def run_apply_patch(
    arguments: dict[str, Any], workspace: str, *, tool_name: str = "apply_patch"
) -> ToolResult:
    workspace_path = Path(workspace).expanduser().resolve()
    patch = arguments.get("patch")
    dry_run = bool_argument(arguments, "dry_run", True)
    create_dirs = bool_argument(arguments, "create_dirs", True)

    if not isinstance(patch, str) or not patch.strip():
        return patch_error_result(
            tool_name=tool_name,
            arguments=arguments,
            dry_run=dry_run,
            error="Missing required string patch.",
        )

    if patch.strip().startswith("*** Begin Patch"):
        return run_codex_apply_patch(
            arguments=arguments,
            workspace_path=workspace_path,
            tool_name=tool_name,
            patch=patch,
            dry_run=dry_run,
            create_dirs=create_dirs,
        )

    return run_unified_apply_patch(
        arguments=arguments,
        workspace_path=workspace_path,
        tool_name=tool_name,
        patch=patch,
        dry_run=dry_run,
        create_dirs=create_dirs,
    )


def run_codex_apply_patch(
    *,
    arguments: dict[str, Any],
    workspace_path: Path,
    tool_name: str,
    patch: str,
    dry_run: bool,
    create_dirs: bool,
) -> ToolResult:
    parsed_patch, parse_error = parse_codex_patch(patch)
    if parse_error:
        return patch_error_result(
            tool_name=tool_name,
            arguments=arguments,
            dry_run=dry_run,
            error=parse_error,
            syntax="codex_patch",
        )

    assert parsed_patch is not None
    planned_changes: list[dict[str, Any]] = []
    file_results: list[dict[str, Any]] = []
    for file_patch in parsed_patch.files:
        plan, error = plan_codex_patch_file(file_patch, workspace_path)
        if error:
            return patch_error_result(
                tool_name=tool_name,
                arguments=arguments,
                dry_run=dry_run,
                error=error["error"],
                path=error.get("path", ""),
                syntax="codex_patch",
            )

        assert plan is not None
        planned_changes.append(plan)
        file_results.append(
            {
                "path": plan["path"],
                "status": plan["status"],
                "hunks": len(file_patch.hunks),
                "old_lines": plan["old_lines"],
                "new_lines": plan["new_lines"],
            }
        )

    if not dry_run and not create_dirs:
        for plan in planned_changes:
            path = plan["write_path"]
            if plan["status"] != "deleted" and not path.parent.exists():
                return patch_error_result(
                    tool_name=tool_name,
                    arguments=arguments,
                    dry_run=dry_run,
                    error=f"Parent directory does not exist: {path.parent}",
                    path=plan["path"],
                    syntax="codex_patch",
                )

    if not dry_run:
        for plan in planned_changes:
            try:
                if plan["status"] == "deleted":
                    plan["delete_path"].unlink()
                    continue

                write_path = plan["write_path"]
                if not write_path.parent.exists():
                    write_path.parent.mkdir(parents=True, exist_ok=True)
                write_path.write_text(plan["content"], encoding="utf-8", newline="")

                if plan["status"] == "moved" and plan["delete_path"] != write_path:
                    plan["delete_path"].unlink()
            except OSError as error:
                return patch_error_result(
                    tool_name=tool_name,
                    arguments=arguments,
                    dry_run=dry_run,
                    error=f"Failed to apply patch: {error}",
                    path=plan["path"],
                    syntax="codex_patch",
                )

    payload = {
        "simulated": False,
        "ok": True,
        "tool": tool_name,
        "syntax": "codex_patch",
        "dry_run": dry_run,
        "files": file_results,
        "summary": patch_summary(file_results),
    }
    return ToolResult(
        name=tool_name,
        arguments=arguments,
        content=json_response(payload),
        success=True,
    )


def run_unified_apply_patch(
    *,
    arguments: dict[str, Any],
    workspace_path: Path,
    tool_name: str,
    patch: str,
    dry_run: bool,
    create_dirs: bool,
) -> ToolResult:
    parsed_files, parse_error = parse_unified_patch(patch)
    if parse_error:
        return patch_error_result(
            tool_name=tool_name,
            arguments=arguments,
            dry_run=dry_run,
            error=parse_error,
            syntax="unified_diff",
        )

    planned_changes: list[dict[str, Any]] = []
    file_results: list[dict[str, Any]] = []
    for file_patch in parsed_files:
        plan, error = plan_patch_file(file_patch, workspace_path)
        if error:
            return patch_error_result(
                tool_name=tool_name,
                arguments=arguments,
                dry_run=dry_run,
                error=error["error"],
                path=error.get("path", ""),
                syntax="unified_diff",
            )

        assert plan is not None
        planned_changes.append(plan)
        file_results.append(
            {
                "path": plan["path"],
                "status": plan["status"],
                "hunks": len(file_patch.hunks),
                "old_lines": plan["old_lines"],
                "new_lines": plan["new_lines"],
            }
        )

    if not dry_run and not create_dirs:
        for plan in planned_changes:
            path = plan["absolute_path"]
            if plan["status"] != "deleted" and not path.parent.exists():
                return patch_error_result(
                    tool_name=tool_name,
                    arguments=arguments,
                    dry_run=dry_run,
                    error=f"Parent directory does not exist: {path.parent}",
                    path=plan["path"],
                    syntax="unified_diff",
                )

    if not dry_run:
        for plan in planned_changes:
            path = plan["absolute_path"]
            try:
                if plan["status"] == "deleted":
                    path.unlink()
                    continue

                if not path.parent.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)

                path.write_text(plan["content"], encoding="utf-8", newline="")
            except OSError as error:
                return patch_error_result(
                    tool_name=tool_name,
                    arguments=arguments,
                    dry_run=dry_run,
                    error=f"Failed to apply patch: {error}",
                    path=plan["path"],
                    syntax="unified_diff",
                )

    payload = {
        "simulated": False,
        "ok": True,
        "tool": tool_name,
        "syntax": "unified_diff",
        "dry_run": dry_run,
        "files": file_results,
        "summary": patch_summary(file_results),
    }
    return ToolResult(
        name=tool_name,
        arguments=arguments,
        content=json_response(payload),
        success=True,
    )


def plan_codex_patch_file(
    file_patch: CodexPatchFile, workspace_path: Path
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    path_result = resolve_file_path(workspace_path, file_patch.path)
    if isinstance(path_result, str):
        return None, {"path": file_patch.path, "error": path_result}

    if file_patch.kind == "added":
        if path_result.exists():
            return None, {
                "path": file_patch.path,
                "error": f"File already exists: {path_result}",
            }
        return (
            {
                "path": file_patch.path,
                "status": "added",
                "content": file_patch.content,
                "delete_path": path_result,
                "write_path": path_result,
                "old_lines": 0,
                "new_lines": len(file_patch.content.splitlines()),
            },
            None,
        )

    if not path_result.exists():
        return None, {
            "path": file_patch.path,
            "error": f"File does not exist: {path_result}",
        }
    if not path_result.is_file():
        return None, {
            "path": file_patch.path,
            "error": f"Path is not a file: {path_result}",
        }

    try:
        original_content = path_result.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None, {
            "path": file_patch.path,
            "error": f"File is not valid UTF-8 text: {path_result}",
        }
    except OSError as error:
        return None, {
            "path": file_patch.path,
            "error": f"Failed to read file: {error}",
        }

    if file_patch.kind == "deleted":
        return (
            {
                "path": file_patch.path,
                "status": "deleted",
                "content": "",
                "delete_path": path_result,
                "write_path": path_result,
                "old_lines": len(original_content.splitlines()),
                "new_lines": 0,
            },
            None,
        )

    new_content, apply_error = apply_codex_hunks_to_content(
        original_content, file_patch.hunks, file_patch.path
    )
    if apply_error:
        return None, {"path": file_patch.path, "error": apply_error}

    write_path = path_result
    display_path = file_patch.path
    if file_patch.move_path:
        move_result = resolve_file_path(workspace_path, file_patch.move_path)
        if isinstance(move_result, str):
            return None, {"path": file_patch.move_path, "error": move_result}
        if move_result.exists() and move_result != path_result:
            return None, {
                "path": file_patch.move_path,
                "error": f"Destination file already exists: {move_result}",
            }
        write_path = move_result
        display_path = file_patch.move_path

    return (
        {
            "path": display_path,
            "status": "moved" if file_patch.move_path else "modified",
            "content": new_content,
            "delete_path": path_result,
            "write_path": write_path,
            "old_lines": len(original_content.splitlines()),
            "new_lines": len(new_content.splitlines()),
        },
        None,
    )


def parse_unified_patch(patch: str) -> tuple[list[PatchFile], str | None]:
    if "GIT binary patch" in patch or re.search(r"^Binary files .+ differ$", patch, re.MULTILINE):
        return [], "Binary patches are not supported."

    normalized_patch = patch.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized_patch.splitlines(keepends=True)
    files: list[PatchFile] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("--- "):
            file_patch, index_or_error = parse_patch_file(lines, index)
            if isinstance(index_or_error, str):
                return [], index_or_error
            files.append(file_patch)
            index = index_or_error
            continue

        index += 1

    if not files:
        return [], "Patch must contain at least one unified diff file header."

    return files, None


def parse_patch_file(
    lines: list[str], start_index: int
) -> tuple[PatchFile, int | str]:
    old_path, old_error = diff_header_path(lines[start_index], "--- ")
    if old_error:
        return PatchFile(None, None, []), old_error

    next_index = start_index + 1
    if next_index >= len(lines) or not lines[next_index].startswith("+++ "):
        return PatchFile(None, None, []), "Malformed patch: missing +++ file header."

    new_path, new_error = diff_header_path(lines[next_index], "+++ ")
    if new_error:
        return PatchFile(None, None, []), new_error

    hunks: list[PatchHunk] = []
    index = next_index + 1
    while index < len(lines):
        line = lines[index]
        if line.startswith("--- "):
            break
        if line.startswith("diff --git ") and hunks:
            break
        if line.startswith("@@ "):
            hunk, index_or_error = parse_patch_hunk(lines, index)
            if isinstance(index_or_error, str):
                return PatchFile(old_path, new_path, hunks), index_or_error
            hunks.append(hunk)
            index = index_or_error
            continue
        if line.strip() == "":
            index += 1
            continue
        if line.startswith(("diff --git ", "index ", "new file mode ", "deleted file mode ")):
            if hunks:
                break
            index += 1
            continue

        return PatchFile(old_path, new_path, hunks), (
            f"Malformed patch: expected hunk header after file header for "
            f"{new_path or old_path or 'unknown file'}."
        )

    if not hunks:
        return PatchFile(old_path, new_path, hunks), (
            f"Patch for {new_path or old_path or 'unknown file'} has no content hunks."
        )

    return PatchFile(old_path, new_path, hunks), index



def plan_patch_file(
    file_patch: PatchFile, workspace_path: Path
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    status = patch_file_status(file_patch)
    if status is None:
        return None, {
            "path": file_patch.new_path or file_patch.old_path or "",
            "error": "Patch must use /dev/null for either add or delete, not both.",
        }

    relative_path = file_patch.new_path if status != "deleted" else file_patch.old_path
    if not relative_path:
        return None, {
            "path": "",
            "error": "Patch file path is missing.",
        }

    path_result = resolve_file_path(workspace_path, relative_path)
    if isinstance(path_result, str):
        return None, {"path": relative_path, "error": path_result}

    if status == "added":
        if path_result.exists():
            return None, {
                "path": relative_path,
                "error": f"File already exists: {path_result}",
            }
        original_content = ""
    else:
        if not path_result.exists():
            return None, {
                "path": relative_path,
                "error": f"File does not exist: {path_result}",
            }
        if not path_result.is_file():
            return None, {
                "path": relative_path,
                "error": f"Path is not a file: {path_result}",
            }
        try:
            original_content = path_result.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return None, {
                "path": relative_path,
                "error": f"File is not valid UTF-8 text: {path_result}",
            }
        except OSError as error:
            return None, {
                "path": relative_path,
                "error": f"Failed to read file: {error}",
            }

    new_content, apply_error = apply_hunks_to_content(
        original_content, file_patch.hunks, relative_path
    )
    if apply_error:
        return None, {"path": relative_path, "error": apply_error}

    return (
        {
            "path": relative_path,
            "absolute_path": path_result,
            "status": status,
            "content": new_content,
            "old_lines": len(original_content.splitlines()),
            "new_lines": 0 if status == "deleted" else len(new_content.splitlines()),
        },
        None,
    )




























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
        process = await process_launcher.spawn(
            bash_path,
            "-lc",
            command,
            cwd=str(cwd_result),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            scope_name="bash-command",
        )
    except SandboxError as error:
        return bash_error_result(
            arguments=arguments,
            command=command,
            cwd=str(cwd_result),
            timeout_seconds=timeout_seconds,
            error=error.public_message,
            shell=bash_path,
            error_code=error.code,
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

    output = await capture_process_output(
        process,
        timeout_seconds,
        stdout_limit=OUTPUT_LIMIT,
        stderr_limit=OUTPUT_LIMIT,
    )
    payload = {
        "simulated": False,
        "ok": output.exit_code == 0,
        "command": command,
        "cwd": str(cwd_result),
        "shell": bash_path,
        "timeout_seconds": timeout_seconds,
        "exit_code": output.exit_code,
        "timed_out": output.timed_out,
        "stdout": output.stdout.text,
        "stderr": output.stderr.text,
        "stdout_truncated": output.stdout.truncated,
        "stderr_truncated": output.stderr.truncated,
        "error_code": (
            output.sandbox_failure.code
            if output.sandbox_failure is not None
            else None
        ),
        "sandbox": output.sandbox.to_dict() if output.sandbox is not None else None,
    }
    return ToolResult(
        name="run_bash",
        arguments=arguments,
        content=json_response(payload),
        success=payload["ok"],
        error_code=payload["error_code"],
        sandbox=payload["sandbox"],
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






def resolve_powershell_executable() -> str | None:
    candidates: tuple[str | None, ...]
    if os.name == "nt":
        candidates = (
            shutil.which("pwsh"),
            r"C:\Program Files\PowerShell\7\pwsh.exe",
            shutil.which("powershell"),
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        )
    else:
        candidates = (
            shutil.which("pwsh"),
            shutil.which("powershell"),
        )

    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate

    return None


def is_wsl_bash(path: str) -> bool:
    normalized = str(Path(path)).lower()
    return (
        "\\windows\\system32\\bash.exe" in normalized
        or "\\windows\\sysnative\\bash.exe" in normalized
    )






