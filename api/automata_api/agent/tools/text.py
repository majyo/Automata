"""Pure text helpers shared by the file tools.

``select_line_range`` picks the requested line window out of a file's
contents; the truncation helpers bound how much of a file the model sees.
None of them touch the filesystem.
"""

from __future__ import annotations

from typing import Any

from automata_api.agent.execution.output import HeadTailTextBuffer
from automata_api.agent.tools.args import positive_int_argument


def select_line_range(
    content: str, raw_start_line: Any, raw_end_line: Any
) -> tuple[str, int | None, int | None, int]:
    lines = content.splitlines(keepends=True)
    total_lines = len(lines)
    start_line = positive_int_argument(raw_start_line)
    end_line = positive_int_argument(raw_end_line)
    if start_line is None and end_line is None:
        return content, None, None, total_lines

    start = start_line if start_line is not None else 1
    end = end_line if end_line is not None else total_lines
    if end < start:
        return "", start, end, total_lines

    return "".join(lines[start - 1 : end]), start, end, total_lines


def truncate_content(content: str, limit: int) -> tuple[str, bool]:
    if len(content) <= limit:
        return content, False

    return content[:limit], True


def truncate_head_tail_content(content: str, limit: int) -> tuple[str, bool]:
    buffer = HeadTailTextBuffer(limit)
    buffer.append(content)
    return buffer.text, buffer.truncated

