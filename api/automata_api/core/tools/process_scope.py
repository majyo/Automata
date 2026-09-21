"""Execution context carried across tool calls; no OS operations."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from automata_api.core.tools.permissions import CompiledPermissionProfile


@dataclass(frozen=True)
class ProcessExecutionScope:
    run_id: str
    tool_call_id: str
    session_id: str | None = None
    workspace: str | None = None
    permission_profile: CompiledPermissionProfile | None = None
    emit_event: Any = None
    sandbox_attempt: int = 1


_process_scope: ContextVar[ProcessExecutionScope | None] = ContextVar(
    "automata_process_scope", default=None
)


@contextmanager
def process_execution_scope(
    run_id: str,
    tool_call_id: str,
    *,
    session_id: str | None = None,
    workspace: str | None = None,
    permission_profile: CompiledPermissionProfile | None = None,
    emit_event: Any = None,
    sandbox_attempt: int = 1,
) -> Iterator[None]:
    token = _process_scope.set(
        ProcessExecutionScope(
            run_id=run_id,
            tool_call_id=tool_call_id,
            session_id=session_id,
            workspace=workspace,
            permission_profile=permission_profile,
            emit_event=emit_event,
            sandbox_attempt=sandbox_attempt,
        )
    )
    try:
        yield
    finally:
        _process_scope.reset(token)


def current_process_scope() -> ProcessExecutionScope | None:
    return _process_scope.get()


_process_resources: ContextVar[tuple[Any, Any] | None] = ContextVar(
    "automata_process_resources", default=None
)


@contextmanager
def process_resources(supervisor: Any, sessions: Any) -> Iterator[None]:
    token = _process_resources.set((supervisor, sessions))
    try:
        yield
    finally:
        _process_resources.reset(token)


def current_process_resources() -> tuple[Any, Any] | None:
    return _process_resources.get()
