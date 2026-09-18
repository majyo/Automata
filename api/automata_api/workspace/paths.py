"""Path resolution and containment rules.

Everything a tool needs to turn a model supplied path into a real path
inside the workspace, or into a diagnostic string explaining why it cannot.
These functions are pure: they read the filesystem only to resolve
symlinks and never mutate anything.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

OUTSIDE_WORKSPACE_MESSAGE = "path must stay inside workspace"


def normalize_workspace(workspace: str) -> Path:
    """Resolve a workspace root the way every tool expects it."""
    return Path(workspace).expanduser().resolve()


def resolve_file_path(workspace_path: Path, raw_path: Any) -> Path | str:
    """Resolve ``raw_path`` inside ``workspace_path``.

    Returns the resolved path, or a human readable string when the request
    is missing or escapes the workspace. Callers distinguish the two by
    ``isinstance(result, str)``.
    """
    requested_path = raw_path if isinstance(raw_path, str) and raw_path.strip() else ""
    if not requested_path:
        return "Missing required path."

    path = Path(requested_path).expanduser()
    if path.is_absolute():
        resolved = path.resolve()
    else:
        resolved = (workspace_path / path).resolve()

    if not is_within(resolved, workspace_path):
        return f"{OUTSIDE_WORKSPACE_MESSAGE}: {workspace_path}"

    return resolved


def resolve_search_path(
    *,
    workspace_path: Path,
    cwd_path: Path,
    raw_path: Any,
) -> Path | str:
    """Resolve a search root, returning a message when it is unusable.

    Like :func:`resolve_file_path`, the string return is a diagnostic for
    the tool to report rather than an exception to handle.
    """
    requested_path = raw_path if isinstance(raw_path, str) and raw_path.strip() else "."
    path = Path(requested_path).expanduser()
    if path.is_absolute():
        resolved = path.resolve()
    else:
        resolved = (cwd_path / path).resolve()

    if not is_within(resolved, workspace_path):
        return f"{OUTSIDE_WORKSPACE_MESSAGE}: {workspace_path}"

    if not resolved.exists():
        return f"path does not exist: {resolved}"

    return resolved


def resolve_tool_cwd(workspace_path: Path, raw_cwd: Any) -> Path | str:
    """Resolve a command working directory, clamped to the workspace.

    Unlike :func:`resolve_search_path`, an unusable directory is reported
    with a specific reason (missing, not a directory) because the model
    sees this message directly.
    """
    requested_cwd = raw_cwd if isinstance(raw_cwd, str) and raw_cwd.strip() else "."
    cwd_path = Path(requested_cwd).expanduser()
    if cwd_path.is_absolute():
        resolved = cwd_path.resolve()
    else:
        resolved = (workspace_path / cwd_path).resolve()

    if not is_within(resolved, workspace_path):
        return f"cwd must stay inside workspace: {workspace_path}"

    if not resolved.exists():
        return f"cwd does not exist: {resolved}"

    if not resolved.is_dir():
        return f"cwd is not a directory: {resolved}"

    return resolved


def is_within(path: Path, workspace_path: Path) -> bool:
    """Return whether ``path`` is the workspace or sits inside it."""
    try:
        path.relative_to(workspace_path)
    except ValueError:
        return False
    return True


def path_argument_for_cwd(path: Path, cwd_path: Path) -> str:
    """Express ``path`` relative to ``cwd_path`` for display in commands."""
    relative = os.path.relpath(path, cwd_path)
    return Path(relative).as_posix()


def resolve_executable(name: str) -> str | None:
    return shutil.which(name)
