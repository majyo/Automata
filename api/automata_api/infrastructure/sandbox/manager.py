from __future__ import annotations

import os
import sys

from automata_api.core.tools.permissions import CompiledPermissionProfile
from automata_api.core.tools.sandbox import SandboxError
from automata_api.infrastructure.sandbox.backends import (
    DirectSandboxBackend,
    LinuxSandboxBackend,
    MacOSSandboxBackend,
    SandboxBackend,
    WindowsSandboxBackend,
)


class SandboxManager:
    def __init__(self) -> None:
        self._direct = DirectSandboxBackend()
        self._windows = WindowsSandboxBackend()
        self._linux = LinuxSandboxBackend()
        self._macos = MacOSSandboxBackend()

    def select(self, profile: CompiledPermissionProfile) -> SandboxBackend:
        if profile.sandbox_enforcement == "disabled":
            return self._direct
        if profile.sandbox_enforcement == "external":
            if os.environ.get("AUTOMATA_EXTERNAL_SANDBOX", "") == "1":
                return self._direct
            raise SandboxError(
                "sandbox_unavailable",
                "The permission profile requires an external sandbox host.",
            )
        if sys.platform == "win32":
            return self._windows
        if sys.platform == "darwin":
            return self._macos
        if sys.platform.startswith("linux"):
            return self._linux
        raise SandboxError(
            "sandbox_policy_unsupported",
            f"Managed sandbox is not supported on platform {sys.platform!r}.",
        )


sandbox_manager = SandboxManager()
