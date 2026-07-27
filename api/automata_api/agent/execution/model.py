import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

ToolRisk = Literal["read", "write", "command", "destructive", "external"]
PolicyAction = Literal["allow", "prompt", "deny"]
ApprovalDecision = Literal["allow_once", "allow_for_run", "deny"]


class PublicRunError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.public_message = message


class RunCancelledError(asyncio.CancelledError):
    pass


class CancellationToken:
    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason = "Run cancelled."

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self, reason: str = "Run cancelled.") -> None:
        if self._event.is_set():
            return
        self._reason = reason
        self._event.set()

    async def wait(self) -> None:
        await self._event.wait()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise RunCancelledError(self._reason)


@dataclass(frozen=True)
class ToolPolicyDecision:
    action: PolicyAction
    risk: ToolRisk
    reason: str
    approval_scope: str | None = None
    allow_for_run: bool = False


@dataclass(frozen=True)
class ToolExecutionContext:
    run_id: str
    session_id: str
    tool_call_id: str
    workspace: str
    mode: Literal["act", "plan"]
    cancellation: CancellationToken
    emit_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None


@dataclass(frozen=True)
class RunOutcome:
    response_content: str | None = None
    plan_content: str | None = None


@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    run_id: str
    session_id: str
    tool_call_id: str
    tool: str
    tool_identity: str
    risk: ToolRisk
    reason: str
    summary: str
    preview: dict[str, Any]
    arguments_hash: str
    scope: str | None
    options: tuple[ApprovalDecision, ...]
    created_at: str
