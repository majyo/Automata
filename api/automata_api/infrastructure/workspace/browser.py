"""Bounded local file previews, with the same containment rule as tools."""

from __future__ import annotations

import codecs
import heapq
import ntpath
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from automata_api.core.workspace import (
    WorkspaceBrowseError,
    WorkspaceDirectory,
    WorkspaceEntry,
    WorkspaceText,
)
from automata_api.infrastructure.workspace.paths import is_within, normalize_workspace

MAX_DIRECTORY_ENTRIES = 2000
MAX_PREVIEW_BYTES = 256 * 1024
MAX_PREVIEW_LINES = 5000


class LocalWorkspaceBrowser:
    def list_directory(self, workspace: str, path: str) -> WorkspaceDirectory:
        root, target, relative = resolve_target(workspace, path)
        try:
            if not target.is_dir():
                raise WorkspaceBrowseError("not_directory", "所选路径不是目录")
            # Scanning stays one level deep; even very large directories do not
            # allocate an unbounded list of entries or traverse their children.
            with os.scandir(target) as items:
                selected = heapq.nsmallest(
                    MAX_DIRECTORY_ENTRIES + 1,
                    items,
                    key=entry_sort_key,
                )
            entries = [
                directory_entry(root, relative, item)
                for item in selected[:MAX_DIRECTORY_ENTRIES]
            ]
        except OSError as error:
            raise filesystem_error(error) from error
        return WorkspaceDirectory(
            workspace=str(root),
            path=relative,
            entries=entries,
            truncated=len(selected) > MAX_DIRECTORY_ENTRIES,
        )

    def read_text(self, workspace: str, path: str) -> WorkspaceText:
        root, target, relative = resolve_target(workspace, path)
        try:
            if not stat.S_ISREG(target.stat().st_mode):
                raise WorkspaceBrowseError("not_file", "请选择普通文本文件")
            with target.open("rb") as stream:
                metadata = os.fstat(stream.fileno())
                data = stream.read(MAX_PREVIEW_BYTES + 1)
        except OSError as error:
            raise filesystem_error(error) from error

        truncated = len(data) > MAX_PREVIEW_BYTES
        content, encoding = decode_text(data[:MAX_PREVIEW_BYTES], truncated=truncated)
        # Bound DOM work as well as bytes, including files containing thousands
        # of empty lines. A partial multibyte character is withheld by the decoder.
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        lines = content.split("\n", MAX_PREVIEW_LINES)
        if len(lines) > MAX_PREVIEW_LINES:
            content = "\n".join(lines[:MAX_PREVIEW_LINES])
            truncated = True
        return WorkspaceText(
            workspace=str(root),
            path=relative,
            content=content,
            encoding=encoding,
            size=metadata.st_size,
            modified_at=timestamp(metadata.st_mtime),
            truncated=truncated,
        )


def resolve_target(workspace: str, path: str) -> tuple[Path, Path, str]:
    if not workspace.strip() or "\x00" in workspace:
        raise WorkspaceBrowseError("invalid_workspace", "请先指定工作目录")
    try:
        root = normalize_workspace(workspace)
        if not root.is_dir():
            raise WorkspaceBrowseError("invalid_workspace", "工作目录不存在或不是目录")
        # Reject drive-relative paths and Windows alternate data streams too.
        raw_path = path.replace("\\", "/") or "."
        if ntpath.isabs(raw_path) or ":" in raw_path or "\x00" in raw_path:
            raise WorkspaceBrowseError(
                "outside_workspace", "路径必须位于当前工作目录内"
            )
        requested = root / raw_path
        resolved = requested.resolve()
        if not is_within(resolved, root):
            raise WorkspaceBrowseError(
                "outside_workspace", "路径必须位于当前工作目录内"
            )
        # Keep the displayed path relative to the requested directory, including
        # in-workspace symlinks, instead of unexpectedly changing the breadcrumbs.
        lexical = Path(os.path.abspath(requested))
        if not is_within(lexical, root):
            raise WorkspaceBrowseError(
                "outside_workspace", "路径必须位于当前工作目录内"
            )
        if not resolved.exists():
            raise WorkspaceBrowseError("not_found", "文件或目录不存在，请刷新目录")
        return root, resolved, lexical.relative_to(root).as_posix()
    except (OSError, RuntimeError) as error:
        if isinstance(error, RuntimeError):
            raise WorkspaceBrowseError("io_error", "无法解析文件路径") from error
        raise filesystem_error(error) from error


def directory_entry(
    root: Path, relative: str, item: os.DirEntry[str]
) -> WorkspaceEntry:
    entry_path = (Path(relative) / item.name).as_posix()
    kind: Literal["directory", "file"] = "file"
    try:
        kind = "directory" if item.is_dir() else "file"
        resolved = Path(item.path).resolve()
        if not is_within(resolved, root):
            raise WorkspaceBrowseError("outside_workspace", "链接指向工作目录之外")
        metadata = item.stat()
        if not stat.S_ISREG(metadata.st_mode) and not stat.S_ISDIR(metadata.st_mode):
            raise WorkspaceBrowseError("not_file", "不支持预览此类文件")
        return WorkspaceEntry(
            name=item.name,
            path=entry_path,
            kind=kind,
            size=None if kind == "directory" else metadata.st_size,
            modified_at=timestamp(metadata.st_mtime),
        )
    except (OSError, RuntimeError, WorkspaceBrowseError) as error:
        return WorkspaceEntry(
            name=item.name,
            path=entry_path,
            kind=kind,
            size=None,
            modified_at=None,
            accessible=False,
            access_error=str(error)
            if isinstance(error, WorkspaceBrowseError)
            else "无法访问此路径",
        )


def entry_sort_key(item: os.DirEntry[str]) -> tuple[bool, str, str]:
    try:
        directory = item.is_dir()
    except OSError:
        directory = False
    return not directory, item.name.casefold(), item.name


def decode_text(data: bytes, *, truncated: bool) -> tuple[str, str]:
    candidates = [("utf-8", "UTF-8"), ("gb18030", "GB18030")]
    for marker, codec, label in (
        (codecs.BOM_UTF32_LE, "utf-32", "UTF-32 LE"),
        (codecs.BOM_UTF32_BE, "utf-32", "UTF-32 BE"),
        (codecs.BOM_UTF16_LE, "utf-16", "UTF-16 LE"),
        (codecs.BOM_UTF16_BE, "utf-16", "UTF-16 BE"),
        (codecs.BOM_UTF8, "utf-8-sig", "UTF-8 BOM"),
    ):
        if data.startswith(marker):
            candidates = [(codec, label)]
            break
    for codec, label in candidates:
        try:
            text = codecs.getincrementaldecoder(codec)(errors="strict").decode(
                data, final=not truncated
            )
        except UnicodeError:
            continue
        if any(
            ord(character) < 32 and character not in "\t\n\r\f" for character in text
        ):
            break
        return text, label
    raise WorkspaceBrowseError(
        "unsupported_text", "此文件是二进制文件或使用了不支持的文本编码"
    )


def timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def filesystem_error(error: OSError) -> WorkspaceBrowseError:
    if isinstance(error, FileNotFoundError):
        return WorkspaceBrowseError("not_found", "文件或目录不存在，请刷新目录")
    if isinstance(error, PermissionError):
        return WorkspaceBrowseError("permission_denied", "没有权限读取此路径")
    return WorkspaceBrowseError("io_error", "无法读取此路径，请刷新后重试")
