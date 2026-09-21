"""Unified-diff parsing and pure patch content transforms.

These are the algorithms behind ``apply_patch``: they parse a unified diff
into files and hunks, validate paths, and rewrite file content. They touch
no filesystem, so they are unit-testable in isolation and reusable by any
workspace implementation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any


@dataclass(frozen=True)
class PatchHunkLine:
    """One line of a hunk; ``kind`` is the diff prefix (space, + or -)."""

    kind: str
    content: str


@dataclass(frozen=True)
class PatchHunk:
    """A single ``@@`` hunk with its header counts."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[PatchHunkLine]


@dataclass(frozen=True)
class PatchFile:
    """One file entry in a unified diff.

    ``old_path``/``new_path`` are ``None`` for ``/dev/null``, which is how
    additions and deletions are expressed.
    """

    old_path: str | None
    new_path: str | None
    hunks: list[PatchHunk]


def parse_patch_hunk(lines: list[str], start_index: int) -> tuple[PatchHunk, int | str]:
    header = lines[start_index].rstrip("\r\n")
    match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", header)
    if not match:
        return PatchHunk(0, 0, 0, 0, []), f"Malformed hunk header: {header}"

    old_start = int(match.group(1))
    old_count = int(match.group(2) or "1")
    new_start = int(match.group(3))
    new_count = int(match.group(4) or "1")
    hunk_lines: list[PatchHunkLine] = []
    index = start_index + 1
    while index < len(lines):
        line = lines[index]
        if (
            line.startswith("@@ ")
            or line.startswith("--- ")
            or line.startswith("diff --git ")
        ):
            break
        if line.startswith("\\ No newline at end of file"):
            index += 1
            continue
        if not line:
            return PatchHunk(0, 0, 0, 0, []), "Malformed patch: empty hunk line."
        kind = line[0]
        if kind not in {" ", "+", "-"}:
            return PatchHunk(0, 0, 0, 0, []), (
                f"Malformed patch: invalid hunk line prefix {kind!r}."
            )
        hunk_lines.append(PatchHunkLine(kind=kind, content=line[1:]))
        index += 1

    observed_old_count = sum(1 for line in hunk_lines if line.kind in {" ", "-"})
    observed_new_count = sum(1 for line in hunk_lines if line.kind in {" ", "+"})
    if observed_old_count != old_count or observed_new_count != new_count:
        return PatchHunk(0, 0, 0, 0, []), (
            "Malformed patch: hunk line counts do not match header "
            f"({observed_old_count}/{old_count} old, "
            f"{observed_new_count}/{new_count} new)."
        )

    return (
        PatchHunk(
            old_start=old_start,
            old_count=old_count,
            new_start=new_start,
            new_count=new_count,
            lines=hunk_lines,
        ),
        index,
    )


def diff_header_path(line: str, prefix: str) -> tuple[str | None, str | None]:
    raw_path = line[len(prefix) :].strip()
    path = raw_path.split("\t", 1)[0].split(" ", 1)[0]
    if path == "/dev/null":
        return None, None

    if len(path) > 2 and path[1] == "/" and path[0] in {"a", "b"}:
        path = path[2:]

    normalized = path.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if not normalized or normalized in {".", "/"}:
        return None, "Patch file path is empty."
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return None, f"Patch file path must be relative: {path}"
    if any(part in {"", ".", ".."} for part in parts):
        return None, f"Patch file path must not escape the workspace: {path}"

    return PurePosixPath(normalized).as_posix(), None


def patch_file_status(file_patch: PatchFile) -> str | None:
    if file_patch.old_path is None and file_patch.new_path is None:
        return None
    if file_patch.old_path is None:
        return "added"
    if file_patch.new_path is None:
        return "deleted"
    return "modified"


def apply_hunks_to_content(
    original_content: str, hunks: list[PatchHunk], relative_path: str
) -> tuple[str, str | None]:
    original_lines = original_content.splitlines(keepends=True)
    new_lines: list[str] = []
    cursor = 0

    for hunk in hunks:
        start_index = max(hunk.old_start - 1, 0)
        if start_index < cursor or start_index > len(original_lines):
            return "", f"Hunk position is invalid for {relative_path}."

        new_lines.extend(original_lines[cursor:start_index])
        cursor = start_index

        for hunk_line in hunk.lines:
            if hunk_line.kind == "+":
                new_lines.append(hunk_line.content)
                continue

            if cursor >= len(original_lines):
                return "", f"Hunk context extends past end of file for {relative_path}."

            if original_lines[cursor] != hunk_line.content:
                return "", (
                    f"Hunk context mismatch for {relative_path} at line {cursor + 1}."
                )

            if hunk_line.kind == " ":
                new_lines.append(original_lines[cursor])
            cursor += 1

    new_lines.extend(original_lines[cursor:])
    return "".join(new_lines), None


def patch_summary(files: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "added": sum(1 for file in files if file["status"] == "added"),
        "modified": sum(1 for file in files if file["status"] == "modified"),
        "deleted": sum(1 for file in files if file["status"] == "deleted"),
        "moved": sum(1 for file in files if file["status"] == "moved"),
        "hunks": sum(int(file["hunks"]) for file in files),
    }


def parse_unified_patch(patch: str) -> tuple[list[PatchFile], str | None]:
    if "GIT binary patch" in patch or re.search(
        r"^Binary files .+ differ$", patch, re.MULTILINE
    ):
        return [], "Binary patches are not supported."

    normalized_patch = patch.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized_patch.splitlines(keepends=True)
    files: list[PatchFile] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("--- "):
            file_patch, index_or_error = parse_patch_file(lines, index)
            if isinstance(index_or_error, str):
                return [], index_or_error
            files.append(file_patch)
            index = index_or_error
            continue

        index += 1

    if not files:
        return [], "Patch must contain at least one unified diff file header."

    return files, None


def parse_patch_file(lines: list[str], start_index: int) -> tuple[PatchFile, int | str]:
    old_path, old_error = diff_header_path(lines[start_index], "--- ")
    if old_error:
        return PatchFile(None, None, []), old_error

    next_index = start_index + 1
    if next_index >= len(lines) or not lines[next_index].startswith("+++ "):
        return PatchFile(None, None, []), "Malformed patch: missing +++ file header."

    new_path, new_error = diff_header_path(lines[next_index], "+++ ")
    if new_error:
        return PatchFile(None, None, []), new_error

    hunks: list[PatchHunk] = []
    index = next_index + 1
    while index < len(lines):
        line = lines[index]
        if line.startswith("--- "):
            break
        if line.startswith("diff --git ") and hunks:
            break
        if line.startswith("@@ "):
            hunk, index_or_error = parse_patch_hunk(lines, index)
            if isinstance(index_or_error, str):
                return PatchFile(old_path, new_path, hunks), index_or_error
            hunks.append(hunk)
            index = index_or_error
            continue
        if line.strip() == "":
            index += 1
            continue
        if line.startswith(
            ("diff --git ", "index ", "new file mode ", "deleted file mode ")
        ):
            if hunks:
                break
            index += 1
            continue

        return PatchFile(old_path, new_path, hunks), (
            f"Malformed patch: expected hunk header after file header for "
            f"{new_path or old_path or 'unknown file'}."
        )

    if not hunks:
        return PatchFile(old_path, new_path, hunks), (
            f"Patch for {new_path or old_path or 'unknown file'} has no content hunks."
        )

    return PatchFile(old_path, new_path, hunks), index
