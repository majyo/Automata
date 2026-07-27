from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from automata_api.agent.execution.sandbox.errors import SandboxErrorCode
from automata_api.agent.execution.sandbox.model import SandboxMetadata

_ERROR_PREFIX = "AUTOMATA_SANDBOX_ERROR:"
_DENIAL_PATTERNS = (
    re.compile(r"(?im)^\s*access is denied\.?\s*$"),
    re.compile(r"(?i)\bpermission denied\b"),
    re.compile(r"(?i)\boperation not permitted\b"),
    re.compile(r"(?i)\bsandbox(?:ed)? process was denied\b"),
)


@dataclass(frozen=True)
class SandboxFailure:
    code: SandboxErrorCode
    message: str
    explicit: bool


def classify_sandbox_failure(
    *,
    exit_code: int | None,
    stderr: str,
    metadata: SandboxMetadata | None,
) -> SandboxFailure | None:
    explicit = _explicit_failure(stderr)
    if explicit is not None:
        return explicit
    if (
        metadata is None
        or metadata.enforcement == "disabled"
        or exit_code in (None, 0)
    ):
        return None
    if any(pattern.search(stderr) for pattern in _DENIAL_PATTERNS):
        return SandboxFailure(
            code="sandbox_denied",
            message="The managed sandbox denied this operation.",
            explicit=False,
        )
    return None


def _explicit_failure(stderr: str) -> SandboxFailure | None:
    for line in stderr.splitlines():
        if not line.startswith(_ERROR_PREFIX):
            continue
        try:
            payload: Any = json.loads(line.removeprefix(_ERROR_PREFIX))
        except json.JSONDecodeError:
            return SandboxFailure(
                code="sandbox_protocol_error",
                message="The sandbox host returned an invalid error response.",
                explicit=True,
            )
        if not isinstance(payload, dict):
            break
        code = payload.get("code")
        message = payload.get("message")
        if code not in {
            "sandbox_unavailable",
            "sandbox_setup_required",
            "sandbox_setup_failed",
            "sandbox_policy_unsupported",
            "sandbox_spawn_failed",
            "sandbox_denied",
            "sandbox_network_denied",
            "sandbox_timed_out",
            "sandbox_protocol_error",
        }:
            code = "sandbox_protocol_error"
        return SandboxFailure(
            code=code,
            message=(
                message
                if isinstance(message, str) and message
                else "The sandbox host rejected this process."
            ),
            explicit=True,
        )
    return None
