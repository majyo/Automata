from __future__ import annotations

from typing import Literal

SandboxErrorCode = Literal[
    "sandbox_unavailable",
    "sandbox_setup_required",
    "sandbox_setup_failed",
    "sandbox_policy_unsupported",
    "sandbox_spawn_failed",
    "sandbox_denied",
    "sandbox_network_denied",
    "sandbox_timed_out",
    "sandbox_protocol_error",
]


class SandboxError(OSError):
    def __init__(self, code: SandboxErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message
