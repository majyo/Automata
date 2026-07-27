from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal

ToolOutputEmitter = Callable[[dict[str, Any]], Awaitable[None]]
ToolOutputStream = Literal["stdout", "stderr"]


@dataclass(frozen=True)
class ToolOutputScope:
    tool_call_id: str
    tool: str
    emit: ToolOutputEmitter


_tool_output_scope: ContextVar[ToolOutputScope | None] = ContextVar(
    "automata_tool_output_scope",
    default=None,
)


@contextmanager
def tool_output_execution_scope(
    *,
    tool_call_id: str,
    tool: str,
    emit: ToolOutputEmitter | None,
) -> Iterator[None]:
    if emit is None:
        yield
        return
    token = _tool_output_scope.set(
        ToolOutputScope(
            tool_call_id=tool_call_id,
            tool=tool,
            emit=emit,
        )
    )
    try:
        yield
    finally:
        _tool_output_scope.reset(token)


async def emit_tool_output(
    stream: ToolOutputStream,
    content: str,
) -> None:
    scope = _tool_output_scope.get()
    if scope is None or not content:
        return
    await scope.emit(
        {
            "type": "tool_output_delta",
            "tool_call_id": scope.tool_call_id,
            "tool": scope.tool,
            "stream": stream,
            "content": content,
        }
    )
