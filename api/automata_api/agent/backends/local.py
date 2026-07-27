from __future__ import annotations

import asyncio
import fnmatch
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from automata_api.agent.backends.base import (
    Backend,
    BackendError,
    ExecResult,
    FileListResult,
    FileStat,
    SearchResult,
)
from automata_api.agent.tools import _core as core


@dataclass(frozen=True)
class PathProcessResult:
    exit_code: int | None
    timed_out: bool
    paths: tuple[str, ...]
    truncated: bool
    truncation_reason: str | None
    stderr: str


class BoundedPathAccumulator:
    def __init__(
        self,
        *,
        normalize: Callable[[str], str | None],
        limit: int,
        max_result_chars: int,
    ) -> None:
        self.normalize = normalize
        self.limit = limit
        self.path_char_budget = max(0, max_result_chars - 4_000)
        self.paths: list[str] = []
        self.seen: set[str] = set()
        self.path_chars = 0
        self.truncated = False
        self.truncation_reason: str | None = None

    def add(self, raw_path: str) -> bool:
        normalized = self.normalize(raw_path)
        if normalized is None or normalized in self.seen:
            return False

        if len(self.paths) >= self.limit:
            self.truncated = True
            self.truncation_reason = "file_limit"
            return True

        encoded_chars = len(json.dumps(normalized, ensure_ascii=True)) + 1
        if self.path_chars + encoded_chars > self.path_char_budget:
            self.truncated = True
            self.truncation_reason = "character_limit"
            return True

        self.seen.add(normalized)
        self.paths.append(normalized)
        self.path_chars += encoded_chars
        return False


class LocalBackend(Backend):
    kind = "local"

    def __init__(self, workspace: str) -> None:
        self.workspace_path = Path(workspace).expanduser().resolve()

    @property
    def workspace_label(self) -> str:
        return str(self.workspace_path)

    async def read_file(self, path: str, *, errors: str = "strict") -> str:
        path_result = self._resolve_file_path(path)
        if not path_result.exists():
            raise BackendError(f"File does not exist: {path_result}")
        if not path_result.is_file():
            raise BackendError(f"Path is not a file: {path_result}")
        try:
            return path_result.read_text(encoding="utf-8", errors=errors)
        except OSError as error:
            raise BackendError(f"Failed to read file: {error}") from error

    async def write_file(
        self,
        path: str,
        content: str,
        *,
        mode: str = "overwrite",
        create_dirs: bool = True,
    ) -> int:
        path_result = self._resolve_file_path(path)
        if mode not in {"overwrite", "create", "append"}:
            raise BackendError("mode must be one of overwrite, create, or append.")
        if path_result.exists() and path_result.is_dir():
            raise BackendError(f"Path is a directory: {path_result}")
        if mode == "create" and path_result.exists():
            raise BackendError(f"File already exists: {path_result}")
        if not path_result.parent.exists():
            if not create_dirs:
                raise BackendError(
                    f"Parent directory does not exist: {path_result.parent}"
                )
            try:
                path_result.parent.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise BackendError(
                    f"Failed to create parent directory: {error}"
                ) from error

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
            raise BackendError(f"Failed to write file: {error}") from error
        return len(content.encode("utf-8"))

    async def delete_file(self, path: str) -> None:
        path_result = self._resolve_file_path(path)
        try:
            path_result.unlink()
        except OSError as error:
            raise BackendError(f"Failed to delete file: {error}") from error

    async def stat(self, path: str) -> FileStat:
        path_result = self._resolve_file_path(path)
        exists = path_result.exists()
        return FileStat(
            exists=exists,
            is_file=path_result.is_file() if exists else False,
            is_dir=path_result.is_dir() if exists else False,
            size_bytes=path_result.stat().st_size if exists else 0,
            path=core.path_argument_for_cwd(path_result, self.workspace_path),
            absolute_path=str(path_result),
        )

    async def parent_exists(self, path: str) -> bool:
        path_result = self._resolve_file_path(path)
        return path_result.parent.exists()

    def parent_label(self, path: str) -> str:
        path_result = self._resolve_file_path(path)
        return str(path_result.parent)

    async def exec_shell(
        self, command: str, *, cwd: str | None = None, timeout_seconds: float
    ) -> ExecResult:
        cwd_path = self._resolve_tool_cwd(cwd)
        bash_path = core.resolve_bash_executable()
        if bash_path is None:
            raise BackendError(
                "Could not find bash. Install Git Bash on Windows or bash on PATH.",
                cwd=str(cwd_path),
            )
        return await self._run_process(
            [bash_path, "-lc", command],
            cwd_path=cwd_path,
            timeout_seconds=timeout_seconds,
            shell=bash_path,
        )

    async def exec_powershell(
        self, command: str, *, cwd: str | None = None, timeout_seconds: float
    ) -> ExecResult:
        cwd_path = self._resolve_tool_cwd(cwd)
        powershell_path = core.resolve_powershell_executable()
        if powershell_path is None:
            raise BackendError(
                "Could not find PowerShell. Install PowerShell or ensure it is on PATH.",
                cwd=str(cwd_path),
            )
        return await self._run_process(
            [
                powershell_path,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
            ],
            cwd_path=cwd_path,
            timeout_seconds=timeout_seconds,
            shell=powershell_path,
        )

    async def run_exec_command(self, arguments: dict[str, Any]):
        return await core.run_exec_command(arguments, self.workspace_label)

    async def search(
        self,
        pattern: str,
        *,
        path: str | None,
        cwd: str | None,
        timeout_seconds: float,
        prefer: str = "rg",
    ) -> SearchResult:
        cwd_path = self._resolve_tool_cwd(cwd)
        search_path = self._resolve_search_path(cwd_path, path)
        engines = ("rg", "grep", "bash") if prefer == "rg" else ("grep", "bash")
        attempts: list[dict[str, Any]] = []

        for engine in engines:
            if engine == "bash":
                return await self._run_bash_search(
                    pattern=pattern,
                    search_path=search_path,
                    cwd_path=cwd_path,
                    timeout_seconds=timeout_seconds,
                    preferred_engine="rg" if prefer == "rg" else "grep",
                    attempts=attempts,
                )

            executable = core.resolve_executable(engine)
            if executable is None:
                attempts.append({"engine": engine, "ok": False, "error": "not found"})
                continue

            result = await self._run_native_search(
                engine=engine,
                executable=executable,
                pattern=pattern,
                search_path=search_path,
                cwd_path=cwd_path,
                timeout_seconds=timeout_seconds,
                attempts=attempts,
            )
            if result.ok:
                return result

        raise BackendError(
            "Could not find a usable search command.",
            cwd=str(cwd_path),
        )

    async def list_files(
        self,
        *,
        path: str | None,
        cwd: str | None,
        include_globs: tuple[str, ...],
        exclude_globs: tuple[str, ...],
        hidden: bool,
        max_depth: int | None,
        limit: int,
        max_result_chars: int,
        timeout_seconds: float,
    ) -> FileListResult:
        cwd_path = self._resolve_tool_cwd(cwd)
        search_path = self._resolve_search_path(cwd_path, path)
        if not search_path.is_dir():
            raise BackendError(
                f"Path is not a directory: {search_path}",
                cwd=str(cwd_path),
            )

        requested_path = core.path_argument_for_cwd(search_path, cwd_path)
        normalize = self._file_list_normalizer(
            cwd_path=cwd_path,
            search_path=search_path,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            hidden=hidden,
            max_depth=max_depth,
        )
        attempts: list[dict[str, Any]] = []

        rg_executable = core.resolve_executable("rg")
        if rg_executable is None:
            attempts.append({"engine": "rg", "ok": False, "error": "not found"})
        else:
            command = [rg_executable, "--files", "--null", "--sort", "path"]
            if hidden:
                command.append("--hidden")
            if max_depth is not None:
                command.extend(["--max-depth", str(max_depth)])
            for pattern in include_globs:
                command.extend(["--glob", pattern])
            for pattern in exclude_globs:
                command.extend(["--glob", f"!{pattern}"])
            command.extend(["--", requested_path])
            result = await self._run_path_process(
                command=command,
                cwd_path=cwd_path,
                output_base=cwd_path,
                normalize=normalize,
                limit=limit,
                max_result_chars=max_result_chars,
                timeout_seconds=timeout_seconds,
            )
            attempts.append(
                {
                    "engine": "rg",
                    "ok": result.truncated
                    or result.exit_code in (0, 1)
                    or (
                        result.timed_out and bool(result.paths)
                    ),
                    "exit_code": result.exit_code,
                    "timed_out": result.timed_out,
                }
            )
            if result.timed_out:
                if not result.paths:
                    raise BackendError(
                        "File enumeration timed out.",
                        cwd=str(cwd_path),
                    )
                return self._file_list_result(
                    engine="rg",
                    path=requested_path,
                    cwd_path=cwd_path,
                    result=result,
                    ignore_semantics="ripgrep",
                    degraded=False,
                    attempts=attempts,
                )
            if result.truncated or result.exit_code in (0, 1):
                return self._file_list_result(
                    engine="rg",
                    path=requested_path,
                    cwd_path=cwd_path,
                    result=result,
                    ignore_semantics="ripgrep",
                    degraded=False,
                    attempts=attempts,
                )

        git_result = await self._try_git_file_list(
            cwd_path=cwd_path,
            search_path=search_path,
            normalize=normalize,
            limit=limit,
            max_result_chars=max_result_chars,
            timeout_seconds=timeout_seconds,
            attempts=attempts,
        )
        if git_result is not None:
            return self._file_list_result(
                engine="git",
                path=requested_path,
                cwd_path=cwd_path,
                result=git_result,
                ignore_semantics="git",
                degraded=False,
                attempts=attempts,
            )

        filesystem_result = await asyncio.to_thread(
            self._walk_filesystem,
            search_path=search_path,
            normalize=normalize,
            hidden=hidden,
            max_depth=max_depth,
            limit=limit,
            max_result_chars=max_result_chars,
            timeout_seconds=timeout_seconds,
        )
        attempts.append(
            {
                "engine": "filesystem",
                "ok": not filesystem_result.timed_out
                or bool(filesystem_result.paths),
                "timed_out": filesystem_result.timed_out,
            }
        )
        if filesystem_result.timed_out and not filesystem_result.paths:
            raise BackendError(
                "File enumeration timed out.",
                cwd=str(cwd_path),
            )
        return self._file_list_result(
            engine="filesystem",
            path=requested_path,
            cwd_path=cwd_path,
            result=filesystem_result,
            ignore_semantics="basic",
            degraded=True,
            attempts=attempts,
        )

    def _resolve_file_path(self, raw_path: Any) -> Path:
        path_result = core.resolve_file_path(self.workspace_path, raw_path)
        if isinstance(path_result, str):
            raise BackendError(path_result)
        return path_result

    def _resolve_tool_cwd(self, raw_cwd: Any) -> Path:
        cwd_result = core.resolve_tool_cwd(self.workspace_path, raw_cwd)
        if isinstance(cwd_result, str):
            raise BackendError(cwd_result, cwd=self.workspace_label)
        return cwd_result

    def _resolve_search_path(self, cwd_path: Path, raw_path: Any) -> Path:
        path_result = core.resolve_search_path(
            workspace_path=self.workspace_path,
            cwd_path=cwd_path,
            raw_path=raw_path,
        )
        if isinstance(path_result, str):
            raise BackendError(path_result, cwd=str(cwd_path))
        return path_result

    async def _run_process(
        self,
        command: list[str],
        *,
        cwd_path: Path,
        timeout_seconds: float,
        shell: str | None,
    ) -> ExecResult:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(cwd_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **core.subprocess_group_kwargs(),
            )
        except OSError as error:
            raise BackendError(
                f"Failed to start process: {error}",
                cwd=str(cwd_path),
                shell=shell,
            ) from error

        output = await core.capture_process_output(
            process,
            timeout_seconds,
            stdout_limit=core.OUTPUT_LIMIT,
            stderr_limit=core.OUTPUT_LIMIT,
        )
        return ExecResult(
            exit_code=output.exit_code,
            timed_out=output.timed_out,
            stdout=output.stdout.text,
            stderr=output.stderr.text,
            stdout_truncated=output.stdout.truncated,
            stderr_truncated=output.stderr.truncated,
            cwd=str(cwd_path),
            shell=shell,
        )

    async def _run_path_process(
        self,
        *,
        command: list[str],
        cwd_path: Path,
        output_base: Path,
        normalize: Callable[[str], str | None],
        limit: int,
        max_result_chars: int,
        timeout_seconds: float,
    ) -> PathProcessResult:
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(cwd_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **core.subprocess_group_kwargs(),
            )
        except OSError as error:
            return PathProcessResult(
                exit_code=None,
                timed_out=False,
                paths=(),
                truncated=False,
                truncation_reason=None,
                stderr=f"Failed to start process: {error}",
            )

        managed = await core.process_supervisor.register(process)
        wait_task = asyncio.create_task(process.wait())
        stderr_task = asyncio.create_task(
            core.read_limited_stream(
                process.stderr,
                core.OUTPUT_LIMIT,
                stream_name=None,
            )
        )
        accumulator = BoundedPathAccumulator(
            normalize=lambda value: normalize(
                str(output_base / value)
            ),
            limit=limit,
            max_result_chars=max_result_chars,
        )
        pending = b""
        timed_out = False
        terminated_for_limit = False
        try:
            async with asyncio.timeout(timeout_seconds):
                while True:
                    if process.stdout is None:
                        break
                    chunk = await process.stdout.read(
                        core.PROCESS_OUTPUT_CHUNK_BYTES
                    )
                    if not chunk:
                        break
                    pending += chunk
                    parts = pending.split(b"\x00")
                    pending = parts.pop()
                    for raw_path in parts:
                        if not raw_path:
                            continue
                        if accumulator.add(
                            raw_path.decode("utf-8", errors="replace")
                        ):
                            terminated_for_limit = True
                            break
                    if terminated_for_limit:
                        break
                if pending and not terminated_for_limit:
                    accumulator.add(
                        pending.decode("utf-8", errors="replace")
                    )
                if terminated_for_limit:
                    await core.process_supervisor.terminate(managed)
                exit_code = await asyncio.shield(wait_task)
        except TimeoutError:
            timed_out = True
            await core.process_supervisor.terminate(managed)
            await asyncio.shield(wait_task)
            exit_code = None
        except asyncio.CancelledError:
            await core.process_supervisor.terminate(managed)
            await asyncio.gather(
                wait_task,
                stderr_task,
                return_exceptions=True,
            )
            raise
        except BaseException:
            await core.process_supervisor.terminate(managed)
            await asyncio.gather(
                wait_task,
                stderr_task,
                return_exceptions=True,
            )
            raise
        finally:
            stderr = await stderr_task
            await core.process_supervisor.unregister(managed)

        truncation_reason = accumulator.truncation_reason
        truncated = accumulator.truncated
        if timed_out:
            truncated = bool(accumulator.paths)
            truncation_reason = "timeout" if accumulator.paths else None
        return PathProcessResult(
            exit_code=exit_code,
            timed_out=timed_out,
            paths=tuple(accumulator.paths),
            truncated=truncated,
            truncation_reason=truncation_reason,
            stderr=stderr.text,
        )

    async def _try_git_file_list(
        self,
        *,
        cwd_path: Path,
        search_path: Path,
        normalize: Callable[[str], str | None],
        limit: int,
        max_result_chars: int,
        timeout_seconds: float,
        attempts: list[dict[str, Any]],
    ) -> PathProcessResult | None:
        git_executable = core.resolve_executable("git")
        if git_executable is None:
            attempts.append(
                {"engine": "git", "ok": False, "error": "not found"}
            )
            return None

        repository_root = await self._git_repository_root(
            git_executable,
            cwd_path,
            timeout_seconds=min(timeout_seconds, 5.0),
        )
        if repository_root is None:
            attempts.append(
                {
                    "engine": "git",
                    "ok": False,
                    "error": "not a git workspace",
                }
            )
            return None
        try:
            search_path.relative_to(repository_root)
        except ValueError:
            attempts.append(
                {
                    "engine": "git",
                    "ok": False,
                    "error": "path is outside repository",
                }
            )
            return None
        relative_path = core.path_argument_for_cwd(
            search_path,
            repository_root,
        )

        command = [
            git_executable,
            "-C",
            str(repository_root),
            "-c",
            "core.quotePath=false",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
            "--full-name",
            "--",
            relative_path,
        ]
        result = await self._run_path_process(
            command=command,
            cwd_path=repository_root,
            output_base=repository_root,
            normalize=normalize,
            limit=limit,
            max_result_chars=max_result_chars,
            timeout_seconds=timeout_seconds,
        )
        attempts.append(
            {
                "engine": "git",
                "ok": result.truncated
                or result.exit_code == 0
                or (result.timed_out and bool(result.paths)),
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
            }
        )
        if result.timed_out:
            if result.paths:
                return result
            raise BackendError(
                "File enumeration timed out.",
                cwd=str(cwd_path),
            )
        return result if result.truncated or result.exit_code == 0 else None

    async def _git_repository_root(
        self,
        executable: str,
        cwd_path: Path,
        *,
        timeout_seconds: float,
    ) -> Path | None:
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                "-C",
                str(cwd_path),
                "rev-parse",
                "--show-toplevel",
                cwd=str(cwd_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **core.subprocess_group_kwargs(),
            )
        except OSError:
            return None
        output = await core.capture_process_output(
            process,
            timeout_seconds,
            stdout_limit=4_096,
            stderr_limit=4_096,
            emit_output=False,
        )
        if output.exit_code != 0 or output.timed_out:
            return None
        raw_root = output.stdout.text.strip()
        if not raw_root:
            return None
        root = Path(raw_root).resolve()
        return root if root.is_dir() else None

    def _walk_filesystem(
        self,
        *,
        search_path: Path,
        normalize: Callable[[str], str | None],
        hidden: bool,
        max_depth: int | None,
        limit: int,
        max_result_chars: int,
        timeout_seconds: float,
    ) -> PathProcessResult:
        accumulator = BoundedPathAccumulator(
            normalize=normalize,
            limit=limit,
            max_result_chars=max_result_chars,
        )
        deadline = time.monotonic() + timeout_seconds
        timed_out = False

        def visit(directory: Path, depth: int) -> bool:
            nonlocal timed_out
            if time.monotonic() >= deadline:
                timed_out = True
                return True
            try:
                entries = sorted(
                    os.scandir(directory),
                    key=lambda entry: entry.name,
                )
            except OSError:
                return False
            for entry in entries:
                if time.monotonic() >= deadline:
                    timed_out = True
                    return True
                try:
                    if entry.is_symlink():
                        continue
                    if not hidden and entry.name.startswith("."):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if max_depth is not None and depth >= max_depth:
                            continue
                        if visit(Path(entry.path), depth + 1):
                            return True
                    elif entry.is_file(follow_symlinks=False):
                        if accumulator.add(entry.path):
                            return True
                except OSError:
                    continue
            return False

        visit(search_path, 0)
        truncated = accumulator.truncated or (
            timed_out and bool(accumulator.paths)
        )
        reason = accumulator.truncation_reason
        if timed_out and accumulator.paths:
            reason = "timeout"
        return PathProcessResult(
            exit_code=0 if not timed_out else None,
            timed_out=timed_out,
            paths=tuple(accumulator.paths),
            truncated=truncated,
            truncation_reason=reason,
            stderr="",
        )

    def _file_list_normalizer(
        self,
        *,
        cwd_path: Path,
        search_path: Path,
        include_globs: tuple[str, ...],
        exclude_globs: tuple[str, ...],
        hidden: bool,
        max_depth: int | None,
    ) -> Callable[[str], str | None]:
        def normalize(raw_path: str) -> str | None:
            raw = Path(raw_path)
            if raw.is_absolute():
                lexical_path = Path(os.path.abspath(raw))
            else:
                lexical_path = Path(os.path.abspath(cwd_path / raw))
            try:
                lexical_relative = lexical_path.relative_to(
                    self.workspace_path
                )
            except ValueError:
                return None
            current = self.workspace_path
            for part in lexical_relative.parts:
                current = current / part
                if current.is_symlink():
                    return None

            try:
                resolved = lexical_path.resolve()
                resolved.relative_to(self.workspace_path)
                relative_to_root = resolved.relative_to(search_path)
            except (OSError, ValueError):
                return None
            if not resolved.is_file():
                return None
            relative_to_workspace = resolved.relative_to(
                self.workspace_path
            )
            if not hidden and any(
                part.startswith(".")
                for part in relative_to_workspace.parts
            ):
                return None
            depth = len(relative_to_root.parts)
            if max_depth is not None and depth > max_depth:
                return None

            normalized = core.path_argument_for_cwd(resolved, cwd_path)
            if include_globs and not any(
                file_list_glob_matches(normalized, pattern)
                for pattern in include_globs
            ):
                return None
            if any(
                file_list_glob_matches(normalized, pattern)
                for pattern in exclude_globs
            ):
                return None
            return normalized

        return normalize

    @staticmethod
    def _file_list_result(
        *,
        engine: str,
        path: str,
        cwd_path: Path,
        result: PathProcessResult,
        ignore_semantics: str,
        degraded: bool,
        attempts: list[dict[str, Any]],
    ) -> FileListResult:
        return FileListResult(
            ok=True,
            engine=engine,
            path=path,
            cwd=str(cwd_path),
            files=tuple(sorted(result.paths)),
            truncated=result.truncated,
            truncation_reason=result.truncation_reason,
            ignore_semantics=ignore_semantics,
            degraded=degraded,
            timed_out=result.timed_out,
            attempts=list(attempts),
        )

    async def _run_native_search(
        self,
        *,
        engine: str,
        executable: str,
        pattern: str,
        search_path: Path,
        cwd_path: Path,
        timeout_seconds: float,
        attempts: list[dict[str, Any]],
    ) -> SearchResult:
        relative_path = core.path_argument_for_cwd(search_path, cwd_path)
        if engine == "rg":
            command = [
                executable,
                "--line-number",
                "--color",
                "never",
                "--",
                pattern,
                relative_path,
            ]
        else:
            command = [executable, "-R", "-n", "--", pattern, relative_path]

        process_result = await core.run_process(command, cwd_path, timeout_seconds)
        attempts.append(
            {
                "engine": engine,
                "ok": core.search_exit_code_is_ok(process_result["exit_code"]),
                "exit_code": process_result["exit_code"],
                "timed_out": process_result["timed_out"],
            }
        )
        exit_code = process_result["exit_code"]
        return SearchResult(
            ok=core.search_exit_code_is_ok(exit_code),
            matched=exit_code == 0,
            engine=engine,
            pattern=pattern,
            path=relative_path,
            cwd=str(cwd_path),
            command=core.display_command(command),
            timeout_seconds=timeout_seconds,
            exit_code=exit_code,
            timed_out=process_result["timed_out"],
            stdout=process_result["stdout"],
            stderr=process_result["stderr"],
            stdout_truncated=process_result["stdout_truncated"],
            stderr_truncated=process_result["stderr_truncated"],
            attempts=attempts,
        )

    async def _run_bash_search(
        self,
        *,
        pattern: str,
        search_path: Path,
        cwd_path: Path,
        timeout_seconds: float,
        preferred_engine: str,
        attempts: list[dict[str, Any]],
    ) -> SearchResult:
        relative_path = core.path_argument_for_cwd(search_path, cwd_path)
        command = core.bash_search_command(preferred_engine, pattern, relative_path)
        result = await self.exec_shell(
            command,
            cwd=core.path_argument_for_cwd(cwd_path, self.workspace_path),
            timeout_seconds=timeout_seconds,
        )
        attempts.append(
            {
                "engine": "bash",
                "ok": core.search_exit_code_is_ok(result.exit_code),
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
            }
        )
        return SearchResult(
            ok=core.search_exit_code_is_ok(result.exit_code),
            matched=result.exit_code == 0,
            engine="bash",
            pattern=pattern,
            path=relative_path,
            cwd=str(cwd_path),
            command=command,
            timeout_seconds=timeout_seconds,
            exit_code=result.exit_code,
            timed_out=result.timed_out,
            stdout=result.stdout,
            stderr=result.stderr,
            stdout_truncated=result.stdout_truncated,
            stderr_truncated=result.stderr_truncated,
            attempts=attempts,
        )


def file_list_glob_matches(path: str, pattern: str) -> bool:
    normalized_pattern = pattern.replace("\\", "/")
    return fnmatch.fnmatchcase(path, normalized_pattern) or (
        "/" not in normalized_pattern
        and fnmatch.fnmatchcase(Path(path).name, normalized_pattern)
    )
