"""Sandbox setup implementation; elevation remains explicit in this operation."""

import hashlib

from automata_api.infrastructure.sandbox.permissions import (
    compile_run_permission_profile,
)
from automata_api.infrastructure.sandbox.setup import (
    prepare_windows_sandbox,
    windows_sandbox_status,
)


class LocalSandboxAdministration:
    def status(self):
        return windows_sandbox_status()

    async def prepare(self, workspace: str):
        setup_key = hashlib.sha256(workspace.encode("utf-8")).hexdigest()
        profile = compile_run_permission_profile(
            "default", workspace=workspace, run_id=f"sandbox-setup:{setup_key}"
        )
        return await prepare_windows_sandbox(profile, allow_elevation=True)
