"""Session domain rules.

Pure policy with no framework, storage or agent dependency: which backends
exist on this platform, how a working directory is normalised, and what
counts as a valid session payload. Storage and transport both call in here
so the rules exist once.
"""

from __future__ import annotations

import os
from pathlib import Path

from automata_api.execution.permissions import (
    DEFAULT_PERMISSION_PRESET,
    PermissionPreset,
    normalize_permission_preset,
)


class SessionDomainError(ValueError):
    """Base class for session rule violations."""


class SessionNotFoundError(SessionDomainError):
    pass


class InvalidWorkingDirectoryError(SessionDomainError):
    pass


class InvalidBackendError(SessionDomainError):
    pass


class InvalidPermissionPresetError(SessionDomainError):
    pass


class PlanNotFoundError(SessionDomainError):
    pass


class PlanStateError(SessionDomainError):
    pass


class SessionHasActiveRunError(SessionDomainError):
    def __init__(self, run_id: str) -> None:
        super().__init__("Session has an active run.")
        self.run_id = run_id


BACKEND_KINDS: tuple[str, ...] = ("local", "windows")
WINDOWS_ONLY_BACKENDS: frozenset[str] = frozenset({"windows"})


def available_backend_kinds() -> tuple[str, ...]:
    """Backend kinds this platform can actually run."""
    if os.name == "nt":
        return BACKEND_KINDS
    return tuple(kind for kind in BACKEND_KINDS if kind not in WINDOWS_ONLY_BACKENDS)


def default_backend_kind() -> str:
    return "windows" if os.name == "nt" else "local"


def is_backend_available(kind: str) -> bool:
    return kind in available_backend_kinds()


def normalize_backend(backend: str | None) -> str:
    raw_value = (
        backend.strip().lower()
        if isinstance(backend, str) and backend.strip()
        else default_backend_kind()
    )
    kinds = available_backend_kinds()
    if raw_value not in kinds:
        allowed = ", ".join(kinds)
        raise InvalidBackendError(
            f"Backend is invalid: {raw_value}. Available backends: {allowed}"
        )
    return raw_value


def normalize_working_directory(
    working_directory: str | None,
    *,
    fallback: str,
) -> str:
    """Resolve a session working directory, defaulting to ``fallback``.

    ``fallback`` is the agent workspace; it is passed in rather than read
    from the environment so this rule stays pure.
    """
    raw_value = (
        working_directory.strip()
        if isinstance(working_directory, str) and working_directory.strip()
        else fallback
    )
    try:
        path = Path(raw_value).expanduser().resolve()
    except OSError as error:
        raise InvalidWorkingDirectoryError(
            f"Working directory is invalid: {raw_value}"
        ) from error

    if not path.exists():
        raise InvalidWorkingDirectoryError(
            f"Working directory does not exist: {path}"
        )
    if not path.is_dir():
        raise InvalidWorkingDirectoryError(
            f"Working directory is not a directory: {path}"
        )

    return str(path)


def normalize_session_permission_preset(value: object) -> PermissionPreset:
    try:
        return normalize_permission_preset(value)
    except ValueError as error:
        raise InvalidPermissionPresetError(str(error)) from error


def default_permission_preset() -> PermissionPreset:
    return DEFAULT_PERMISSION_PRESET
