from __future__ import annotations

from automata_api.infrastructure.workspace.backends.local import LocalBackend


class WindowsBackend(LocalBackend):
    kind = "windows"

    def prompt_notes(self) -> str:
        return (
            f"{super().prompt_notes()}\n\n"
            "This Windows backend also exposes run_powershell for "
            "PowerShell-specific commands in the workspace."
        )
