from __future__ import annotations

from automata_api.agent.backends.local import LocalBackend
from automata_api.agent.tools.powershell import RunPowershellTool


class WindowsBackend(LocalBackend):
    kind = "windows"

    def tools(self):
        return (*super().tools(), RunPowershellTool(self))

    def prompt_notes(self) -> str:
        return (
            f"{super().prompt_notes()}\n\n"
            "This Windows backend also exposes run_powershell for "
            "PowerShell-specific commands in the workspace."
        )
