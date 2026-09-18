"""Shell selection for command execution.

``exec_command`` runs under bash or PowerShell; these helpers decide which
shell a request means and locate its executable. Kept separate from
``tools/sessions`` because the backend needs them without any tool result
machinery.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from automata_api.agent.execution.processes import (
    resolve_bash_executable,
    resolve_powershell_executable,
)
from automata_api.agent.tools.constants import SUPPORTED_EXEC_SHELLS


@dataclass(frozen=True)
class ExecShellResolution:
    shell: str
    path: str | None
    error: str | None = None

    @classmethod
    def error_result(cls, shell: str, error: str) -> "ExecShellResolution":
        return cls(shell=shell, path=None, error=error)

    def argv(self, cmd: str) -> list[str]:
        if self.path is None:
            return []
        if self.shell == "bash":
            return [self.path, "-lc", cmd]
        if self.shell == "powershell":
            return [
                self.path,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                cmd,
            ]
        return []


def shell_argument(arguments: dict[str, Any]) -> str:
    raw_value = arguments.get("shell", "bash")
    if not isinstance(raw_value, str) or not raw_value.strip():
        return "bash"

    return raw_value.strip().lower()


def resolve_exec_shell(shell: str) -> ExecShellResolution:
    if shell == "bash":
        bash_path = resolve_bash_executable()
        if bash_path is None:
            return ExecShellResolution.error_result(
                shell=shell,
                error=(
                    "Could not find bash. Install Git Bash on Windows or bash on PATH."
                ),
            )
        return ExecShellResolution(shell=shell, path=bash_path)

    if shell == "powershell":
        powershell_path = resolve_powershell_executable()
        if powershell_path is None:
            return ExecShellResolution.error_result(
                shell=shell,
                error=(
                    "Could not find PowerShell. Install PowerShell or ensure it is on PATH."
                ),
            )
        return ExecShellResolution(shell=shell, path=powershell_path)

    return ExecShellResolution.error_result(
        shell=shell,
        error=(
            f"Unsupported shell: {shell}. Supported shells: "
            f"{', '.join(SUPPORTED_EXEC_SHELLS)}."
        ),
    )

