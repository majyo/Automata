from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, TYPE_CHECKING

if TYPE_CHECKING:
    from automata_api.agent.tools.base import AgentTool


@dataclass(frozen=True)
class ExecResult:
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    cwd: str
    shell: str | None


@dataclass(frozen=True)
class FileStat:
    exists: bool
    is_file: bool
    is_dir: bool
    size_bytes: int
    path: str
    absolute_path: str


@dataclass(frozen=True)
class SearchResult:
    ok: bool
    matched: bool
    engine: str
    pattern: str
    path: str
    cwd: str
    command: str
    timeout_seconds: float
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    attempts: list[dict[str, Any]]


class BackendError(RuntimeError):
    """Primitive-level failure raised by backend implementations."""

    def __init__(
        self, message: str, *, cwd: str | None = None, shell: str | None = None
    ) -> None:
        super().__init__(message)
        self.cwd = cwd
        self.shell = shell


class Backend(ABC):
    kind: ClassVar[str]

    @property
    @abstractmethod
    def workspace_label(self) -> str:
        """Human-readable workspace identifier for prompts and tool results."""

    @abstractmethod
    async def read_file(self, path: str, *, errors: str = "strict") -> str:
        """Read a UTF-8 text file."""

    @abstractmethod
    async def write_file(
        self,
        path: str,
        content: str,
        *,
        mode: str = "overwrite",
        create_dirs: bool = True,
    ) -> int:
        """Write UTF-8 text and return bytes written."""

    @abstractmethod
    async def delete_file(self, path: str) -> None:
        """Delete a file."""

    @abstractmethod
    async def stat(self, path: str) -> FileStat:
        """Return path metadata without requiring the path to exist."""

    @abstractmethod
    async def parent_exists(self, path: str) -> bool:
        """Return whether the parent directory for a path exists."""

    @abstractmethod
    def parent_label(self, path: str) -> str:
        """Return a display label for the parent directory of a path."""

    @abstractmethod
    async def exec_shell(
        self, command: str, *, cwd: str | None = None, timeout_seconds: float
    ) -> ExecResult:
        """Execute a bash-compatible shell command."""

    @abstractmethod
    async def search(
        self,
        pattern: str,
        *,
        path: str | None,
        cwd: str | None,
        timeout_seconds: float,
        prefer: str = "rg",
    ) -> SearchResult:
        """Search workspace files."""

    def tools(self) -> tuple["AgentTool", ...]:
        from automata_api.agent.tools.registry import default_tools

        return default_tools(self)

    def prompt_notes(self) -> str:
        return (
            "Use exec_command to execute real shell commands inside the workspace. "
            "Choose shell=bash for POSIX shell scripts and shell=powershell for "
            "PowerShell scripts. exec_command results have simulated=false and may "
            "have command side effects. Prefer exec_command for checks and tests. "
            "For a long-running non-PTY command, set yield_time_ms and use "
            "write_stdin to poll or write to its stdin pipe.\n\n"
            "run_bash remains available as a compatibility tool for bash-only "
            "commands, but prefer exec_command for new command execution.\n\n"
            "For code or text search, prefer the rg tool first. It automatically "
            "falls back to grep and then to run_bash when needed. Use grep directly "
            "only when grep behavior is specifically required.\n\n"
            "Use read_file to inspect exact file contents and write_file only when "
            "the user explicitly asks you to create or change files. Both operate "
            "on real workspace files and return simulated=false.\n\n"
            "Use apply_patch for targeted code edits with Codex-style patches. A "
            "patch must start with *** Begin Patch and end with *** End Patch. Use "
            "*** Add File, *** Update File, and *** Delete File sections. Update "
            "hunks use @@ without line numbers and must include enough surrounding "
            "context to match uniquely. When practical, call apply_patch with "
            "dry_run=true before applying changes with dry_run=false. Only claim "
            "files were changed after apply_patch or write_file returns "
            "simulated=false and ok=true."
        )

    async def __aenter__(self) -> "Backend":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None
