"""Read-only workspace browsing contracts for the desktop file panel."""

from dataclasses import dataclass
from typing import Literal, Protocol


class WorkspaceBrowseError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WorkspaceEntry:
    name: str
    path: str
    kind: Literal["directory", "file"]
    size: int | None
    modified_at: str | None
    accessible: bool = True
    access_error: str | None = None


@dataclass(frozen=True)
class WorkspaceDirectory:
    workspace: str
    path: str
    entries: list[WorkspaceEntry]
    truncated: bool


@dataclass(frozen=True)
class WorkspaceText:
    workspace: str
    path: str
    content: str
    encoding: str
    size: int
    modified_at: str
    truncated: bool


class WorkspaceBrowser(Protocol):
    def list_directory(self, workspace: str, path: str) -> WorkspaceDirectory: ...
    def read_text(self, workspace: str, path: str) -> WorkspaceText: ...
