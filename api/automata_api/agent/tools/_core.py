import asyncio
import json
import os
import re
import shutil
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


DEFAULT_BASH_TIMEOUT_SECONDS = 30.0
MAX_BASH_TIMEOUT_SECONDS = 120.0
OUTPUT_LIMIT = 20_000
SEARCH_TIMEOUT_SECONDS = 30.0
FILE_READ_LIMIT = 120_000


@dataclass(frozen=True)
class ToolResult:
    name: str
    arguments: dict[str, Any]
    content: str
    success: bool


@dataclass(frozen=True)
class PatchHunkLine:
    kind: str
    content: str


@dataclass(frozen=True)
class PatchHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[PatchHunkLine]


@dataclass(frozen=True)
class PatchFile:
    old_path: str | None
    new_path: str | None
    hunks: list[PatchHunk]


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

    parsed_files, parse_error = parse_unified_patch(patch)
    if parse_error:
        return patch_error_result(
            tool_name=tool_name,
            arguments=arguments,
            dry_run=dry_run,
            error=parse_error,
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
                )

    payload = {
        "simulated": False,
        "ok": True,
        "tool": tool_name,
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


def parse_patch_hunk(lines: list[str], start_index: int) -> tuple[PatchHunk, int | str]:
    header = lines[start_index].rstrip("\r\n")
    match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", header)
    if not match:
        return PatchHunk(0, 0, 0, 0, []), f"Malformed hunk header: {header}"

    old_start = int(match.group(1))
    old_count = int(match.group(2) or "1")
    new_start = int(match.group(3))
    new_count = int(match.group(4) or "1")
    hunk_lines: list[PatchHunkLine] = []
    index = start_index + 1
    while index < len(lines):
        line = lines[index]
        if line.startswith("@@ ") or line.startswith("--- ") or line.startswith("diff --git "):
            break
        if line.startswith("\\ No newline at end of file"):
            index += 1
            continue
        if not line:
            return PatchHunk(0, 0, 0, 0, []), "Malformed patch: empty hunk line."
        kind = line[0]
        if kind not in {" ", "+", "-"}:
            return PatchHunk(0, 0, 0, 0, []), (
                f"Malformed patch: invalid hunk line prefix {kind!r}."
            )
        hunk_lines.append(PatchHunkLine(kind=kind, content=line[1:]))
        index += 1

    observed_old_count = sum(1 for line in hunk_lines if line.kind in {" ", "-"})
    observed_new_count = sum(1 for line in hunk_lines if line.kind in {" ", "+"})
    if observed_old_count != old_count or observed_new_count != new_count:
        return PatchHunk(0, 0, 0, 0, []), (
            "Malformed patch: hunk line counts do not match header "
            f"({observed_old_count}/{old_count} old, "
            f"{observed_new_count}/{new_count} new)."
        )

    return (
        PatchHunk(
            old_start=old_start,
            old_count=old_count,
            new_start=new_start,
            new_count=new_count,
            lines=hunk_lines,
        ),
        index,
    )


def diff_header_path(line: str, prefix: str) -> tuple[str | None, str | None]:
    raw_path = line[len(prefix) :].strip()
    path = raw_path.split("\t", 1)[0].split(" ", 1)[0]
    if path == "/dev/null":
        return None, None

    if len(path) > 2 and path[1] == "/" and path[0] in {"a", "b"}:
        path = path[2:]

    normalized = path.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if not normalized or normalized in {".", "/"}:
        return None, "Patch file path is empty."
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return None, f"Patch file path must be relative: {path}"
    if any(part in {"", ".", ".."} for part in parts):
        return None, f"Patch file path must not escape the workspace: {path}"

    return PurePosixPath(normalized).as_posix(), None


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


def patch_file_status(file_patch: PatchFile) -> str | None:
    if file_patch.old_path is None and file_patch.new_path is None:
        return None
    if file_patch.old_path is None:
        return "added"
    if file_patch.new_path is None:
        return "deleted"
    return "modified"


def apply_hunks_to_content(
    original_content: str, hunks: list[PatchHunk], relative_path: str
) -> tuple[str, str | None]:
    original_lines = original_content.splitlines(keepends=True)
    new_lines: list[str] = []
    cursor = 0

    for hunk in hunks:
        start_index = max(hunk.old_start - 1, 0)
        if start_index < cursor or start_index > len(original_lines):
            return "", f"Hunk position is invalid for {relative_path}."

        new_lines.extend(original_lines[cursor:start_index])
        cursor = start_index

        for hunk_line in hunk.lines:
            if hunk_line.kind == "+":
                new_lines.append(hunk_line.content)
                continue

            if cursor >= len(original_lines):
                return "", f"Hunk context extends past end of file for {relative_path}."

            if original_lines[cursor] != hunk_line.content:
                return "", (
                    f"Hunk context mismatch for {relative_path} at line {cursor + 1}."
                )

            if hunk_line.kind == " ":
                new_lines.append(original_lines[cursor])
            cursor += 1

    new_lines.extend(original_lines[cursor:])
    return "".join(new_lines), None


def patch_summary(files: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "added": sum(1 for file in files if file["status"] == "added"),
        "modified": sum(1 for file in files if file["status"] == "modified"),
        "deleted": sum(1 for file in files if file["status"] == "deleted"),
        "hunks": sum(int(file["hunks"]) for file in files),
    }


def patch_error_result(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    dry_run: bool,
    error: str,
    path: str = "",
) -> ToolResult:
    return ToolResult(
        name=tool_name,
        arguments=arguments,
        content=json_response(
            {
                "simulated": False,
                "ok": False,
                "tool": tool_name,
                "dry_run": dry_run,
                "path": path,
                "error": error,
            }
        ),
        success=False,
    )


def resolve_file_path(workspace_path: Path, raw_path: Any) -> Path | str:
    requested_path = raw_path if isinstance(raw_path, str) and raw_path.strip() else ""
    if not requested_path:
        return "Missing required path."

    path = Path(requested_path).expanduser()
    if path.is_absolute():
        resolved = path.resolve()
    else:
        resolved = (workspace_path / path).resolve()

    try:
        resolved.relative_to(workspace_path)
    except ValueError:
        return f"path must stay inside workspace: {workspace_path}"

    return resolved


def select_line_range(
    content: str, raw_start_line: Any, raw_end_line: Any
) -> tuple[str, int | None, int | None, int]:
    lines = content.splitlines(keepends=True)
    total_lines = len(lines)
    start_line = positive_int_argument(raw_start_line)
    end_line = positive_int_argument(raw_end_line)
    if start_line is None and end_line is None:
        return content, None, None, total_lines

    start = start_line if start_line is not None else 1
    end = end_line if end_line is not None else total_lines
    if end < start:
        return "", start, end, total_lines

    return "".join(lines[start - 1 : end]), start, end, total_lines


def positive_int_argument(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


def bool_argument(arguments: dict[str, Any], name: str, default: bool) -> bool:
    value = arguments.get(name)
    return value if isinstance(value, bool) else default


def truncate_content(content: str, limit: int) -> tuple[str, bool]:
    if len(content) <= limit:
        return content, False

    return content[:limit], True


def file_error_result(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    error: str,
    path: Path | None = None,
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
            }
        ),
        success=False,
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


def string_argument(
    arguments: dict[str, Any], name: str, default: str
) -> str:
    value = arguments.get(name)
    return value if isinstance(value, str) and value.strip() else default


def json_response(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True)
