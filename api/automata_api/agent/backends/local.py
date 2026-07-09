from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from automata_api.agent.backends.base import (
    Backend,
    BackendError,
    ExecResult,
    FileStat,
    SearchResult,
)
from automata_api.agent.tools import _core as core


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
