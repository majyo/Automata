"""Process execution helpers used by workspace and tool code.

These are the pieces that spawn a bounded child process and describe its
result as plain data. They live in the execution layer so that neither the
local backend nor the search implementation has to reach into the agent tool
package for them.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import shutil
from pathlib import Path
from typing import Any

from automata_api.core.tools.constants import OUTPUT_LIMIT
from automata_api.core.tools.sandbox import SandboxError
from automata_api.infrastructure.processes.output import capture_process_output
from automata_api.infrastructure.sandbox import process_launcher


async def run_process(
    command: list[str], cwd_path: Path, timeout_seconds: float
) -> dict[str, Any]:
    """Run ``command`` to completion and describe the result as a mapping.

    Spawn and capture failures are reported in the mapping rather than
    raised: every caller here is building a tool or search result that the
    model reads, so a diagnostic beats an exception.
    """
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
            output.sandbox_failure.code if output.sandbox_failure is not None else None
        ),
        "sandbox": output.sandbox.to_dict() if output.sandbox is not None else None,
    }


def display_command(command: list[str]) -> str:
    """Render a command for the model, quoting each argument."""
    return " ".join(shlex.quote(part) for part in command)


def search_exit_code_is_ok(exit_code: Any) -> bool:
    """``grep``/``rg`` exit 1 means "no match", which is still a success."""
    return exit_code in (0, 1)


def bash_search_command(preferred_engine: str, pattern: str, path: str) -> str:
    """Build a POSIX shell command that searches with rg, then grep."""
    quoted_pattern = shlex.quote(pattern)
    quoted_path = shlex.quote(path)
    if preferred_engine == "rg":
        return (
            "if command -v rg >/dev/null 2>&1; then "
            f"rg --line-number --color never -- {quoted_pattern} {quoted_path}; "
            'status=$?; if [ "$status" -le 1 ]; then exit "$status"; fi; '
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


def is_wsl_bash(path: str) -> bool:
    """Return whether ``path`` is the WSL shim rather than a real bash."""
    normalized = str(Path(path)).lower()
    return (
        "\\windows\\system32\\bash.exe" in normalized
        or "\\windows\\sysnative\\bash.exe" in normalized
    )


def resolve_bash_executable() -> str | None:
    """Find a usable bash, preferring Git for Windows over the WSL shim."""
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
    """Find PowerShell, preferring PowerShell 7 over Windows PowerShell."""
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
