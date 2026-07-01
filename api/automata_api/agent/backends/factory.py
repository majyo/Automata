from __future__ import annotations

import os

from automata_api.agent.backends.base import Backend
from automata_api.agent.backends.local import LocalBackend
from automata_api.agent.backends.windows import WindowsBackend


class BackendConfigurationError(ValueError):
    pass


BACKEND_KINDS = {
    "local": LocalBackend,
    "windows": WindowsBackend,
}


def create_backend(kind: str, *, workspace: str) -> Backend:
    normalized = kind.strip().lower() if isinstance(kind, str) else ""
    if normalized not in BACKEND_KINDS:
        raise BackendConfigurationError(f"Unknown backend: {kind}")
    if normalized == "windows" and os.name != "nt":
        raise BackendConfigurationError("Backend 'windows' is only available on Windows.")
    return BACKEND_KINDS[normalized](workspace)


def default_backend_kind() -> str:
    return "windows" if os.name == "nt" else "local"


def available_backend_kinds() -> tuple[str, ...]:
    if os.name == "nt":
        return ("local", "windows")
    return ("local",)
