import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

BEGIN_PATCH_MARKER = "*** Begin Patch"
END_PATCH_MARKER = "*** End Patch"
ADD_FILE_MARKER = "*** Add File: "
DELETE_FILE_MARKER = "*** Delete File: "
UPDATE_FILE_MARKER = "*** Update File: "
MOVE_TO_MARKER = "*** Move to: "
EOF_MARKER = "*** End of File"
EMPTY_CHANGE_CONTEXT_MARKER = "@@"
CHANGE_CONTEXT_MARKER = "@@ "


CodexOperationKind = Literal["added", "modified", "deleted", "moved"]
CodexLineKind = Literal["context", "remove", "add"]


@dataclass(frozen=True)
class CodexPatchLine:
    kind: CodexLineKind
    content: str


@dataclass(frozen=True)
class CodexPatchHunk:
    context: str | None
    lines: list[CodexPatchLine]
    is_end_of_file: bool = False


@dataclass(frozen=True)
class CodexPatchFile:
    kind: CodexOperationKind
    path: str
    move_path: str | None
    hunks: list[CodexPatchHunk]
    content: str = ""


@dataclass(frozen=True)
class CodexPatch:
    files: list[CodexPatchFile]


def parse_codex_patch(patch: str) -> tuple[CodexPatch | None, str | None]:
    normalized = patch.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return None, "Missing required string patch."

    lines = normalized.split("\n")
    if not lines or lines[0].strip() != BEGIN_PATCH_MARKER:
        return None, f"The first line of the patch must be '{BEGIN_PATCH_MARKER}'."
    if lines[-1].strip() != END_PATCH_MARKER:
        return None, f"The last line of the patch must be '{END_PATCH_MARKER}'."

    body = lines[1:-1]
    if not body:
        return None, "Patch must contain at least one file operation."

    files: list[CodexPatchFile] = []
    index = 0
    while index < len(body):
        if body[index].strip() == "":
            index += 1
            continue
        file_patch, next_index, error = parse_codex_file(body, index)
        if error:
            return None, error
        assert file_patch is not None
        files.append(file_patch)
        index = next_index

    if not files:
        return None, "Patch must contain at least one file operation."

    return CodexPatch(files=files), None


def parse_codex_file(
    lines: list[str], start_index: int
) -> tuple[CodexPatchFile | None, int, str | None]:
    line = lines[start_index].strip()
    line_number = start_index + 2

    if line.startswith(ADD_FILE_MARKER):
        path = line[len(ADD_FILE_MARKER) :]
        normalized_path, path_error = normalize_codex_path(path)
        if path_error:
            return None, start_index, path_error

        content_lines: list[str] = []
        index = start_index + 1
        while index < len(lines):
            if is_file_operation_header(lines[index]):
                break
            if not lines[index].startswith("+"):
                return (
                    None,
                    start_index,
                    f"Invalid add file line at line {index + 2}: lines must start with '+'.",
                )
            content_lines.append(lines[index][1:])
            index += 1

        content = "\n".join(content_lines)
        if content_lines:
            content += "\n"
        return (
            CodexPatchFile(
                kind="added",
                path=normalized_path,
                move_path=None,
                hunks=[],
                content=content,
            ),
            index,
            None,
        )

    if line.startswith(DELETE_FILE_MARKER):
        path = line[len(DELETE_FILE_MARKER) :]
        normalized_path, path_error = normalize_codex_path(path)
        if path_error:
            return None, start_index, path_error
        return (
            CodexPatchFile(
                kind="deleted",
                path=normalized_path,
                move_path=None,
                hunks=[],
            ),
            start_index + 1,
            None,
        )

    if line.startswith(UPDATE_FILE_MARKER):
        path = line[len(UPDATE_FILE_MARKER) :]
        normalized_path, path_error = normalize_codex_path(path)
        if path_error:
            return None, start_index, path_error

        index = start_index + 1
        move_path = None
        if index < len(lines) and lines[index].startswith(MOVE_TO_MARKER):
            raw_move_path = lines[index][len(MOVE_TO_MARKER) :].strip()
            move_path, path_error = normalize_codex_path(raw_move_path)
            if path_error:
                return None, start_index, path_error
            index += 1

        hunks: list[CodexPatchHunk] = []
        while index < len(lines):
            if lines[index].strip() == "":
                index += 1
                continue
            if is_file_operation_header(lines[index]):
                break
            hunk, next_index, hunk_error = parse_codex_hunk(lines, index)
            if hunk_error:
                return None, start_index, hunk_error
            assert hunk is not None
            hunks.append(hunk)
            index = next_index

        if not hunks and move_path is None:
            return (
                None,
                start_index,
                f"Update file hunk for path '{normalized_path}' is empty at line {line_number}.",
            )

        return (
            CodexPatchFile(
                kind="moved" if move_path else "modified",
                path=normalized_path,
                move_path=move_path,
                hunks=hunks,
            ),
            index,
            None,
        )

    return (
        None,
        start_index,
        (
            f"Invalid patch hunk at line {line_number}: {line!r}. Valid hunk headers are "
            "'*** Add File: {path}', '*** Delete File: {path}', and "
            "'*** Update File: {path}'."
        ),
    )


def parse_codex_hunk(
    lines: list[str], start_index: int
) -> tuple[CodexPatchHunk | None, int, str | None]:
    line = lines[start_index]
    line_number = start_index + 2
    if line == EMPTY_CHANGE_CONTEXT_MARKER:
        context = None
    elif line.startswith(CHANGE_CONTEXT_MARKER):
        context = line[len(CHANGE_CONTEXT_MARKER) :]
    else:
        return None, start_index, f"Expected update hunk to start with '@@' at line {line_number}."

    hunk_lines: list[CodexPatchLine] = []
    index = start_index + 1
    is_end_of_file = False
    while index < len(lines):
        current_line = lines[index]
        if current_line == EOF_MARKER:
            is_end_of_file = True
            index += 1
            break
        if current_line == EMPTY_CHANGE_CONTEXT_MARKER or current_line.startswith(
            CHANGE_CONTEXT_MARKER
        ):
            break
        if is_file_operation_header(current_line):
            break

        if current_line == "":
            hunk_lines.append(CodexPatchLine(kind="context", content="\n"))
            index += 1
            continue

        prefix = current_line[0]
        content = current_line[1:] + "\n"
        if prefix == " ":
            hunk_lines.append(CodexPatchLine(kind="context", content=content))
        elif prefix == "-":
            hunk_lines.append(CodexPatchLine(kind="remove", content=content))
        elif prefix == "+":
            hunk_lines.append(CodexPatchLine(kind="add", content=content))
        else:
            return (
                None,
                start_index,
                (
                    f"Unexpected line in update hunk at line {index + 2}: "
                    "every line must start with ' ', '+', or '-'."
                ),
            )
        index += 1

    if not hunk_lines:
        return None, start_index, f"Update hunk does not contain any lines at line {line_number}."

    return (
        CodexPatchHunk(
            context=context,
            lines=hunk_lines,
            is_end_of_file=is_end_of_file,
        ),
        index,
        None,
    )


def apply_codex_hunks_to_content(
    original_content: str, hunks: list[CodexPatchHunk], relative_path: str
) -> tuple[str, str | None]:
    content = original_content
    search_start = 0

    for hunk in hunks:
        old_text = "".join(
            line.content for line in hunk.lines if line.kind in {"context", "remove"}
        )
        new_text = "".join(
            line.content for line in hunk.lines if line.kind in {"context", "add"}
        )

        if not old_text:
            return "", f"Hunk for {relative_path} must include context or removed lines."

        context_start = search_start
        if hunk.context:
            context_index = content.find(hunk.context, search_start)
            if context_index == -1:
                return "", f"Hunk context marker not found for {relative_path}: {hunk.context}"
            context_start = context_index + len(hunk.context)

        matches = find_all_occurrences(content, old_text, context_start)
        if hunk.is_end_of_file:
            stripped_content = content.rstrip("\n")
            stripped_old_text = old_text.rstrip("\n")
            matches = [
                match
                for match in matches
                if stripped_content.endswith(stripped_old_text)
                and match + len(stripped_old_text) == len(stripped_content)
            ]

        if not matches:
            return "", f"Failed to find expected lines in {relative_path}:\n{old_text}"
        if len(matches) > 1:
            return (
                "",
                f"Hunk context is not unique for {relative_path}. Add more surrounding context.",
            )

        match_start = matches[0]
        match_end = match_start + len(old_text)
        content = content[:match_start] + new_text + content[match_end:]
        search_start = match_start + len(new_text)

    return content, None


def find_all_occurrences(content: str, needle: str, start: int = 0) -> list[int]:
    matches: list[int] = []
    index = content.find(needle, start)
    while index != -1:
        matches.append(index)
        index = content.find(needle, index + 1)
    return matches


def normalize_codex_path(path: str) -> tuple[str, str | None]:
    candidate = path.strip()
    normalized = candidate.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if not normalized or normalized in {".", "/"}:
        return "", "Patch file path is empty."
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return "", f"Patch file path must be relative: {candidate}"
    if any(part in {"", ".", ".."} for part in parts):
        return "", f"Patch file path must not escape the workspace: {candidate}"
    return PurePosixPath(normalized).as_posix(), None


def is_file_operation_header(line: str) -> bool:
    return line.startswith((ADD_FILE_MARKER, DELETE_FILE_MARKER, UPDATE_FILE_MARKER))
