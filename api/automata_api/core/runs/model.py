import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from automata_api.core.tools.policy import PolicyAction as PolicyAction
from automata_api.core.tools.policy import ToolPolicyDecision as ToolPolicyDecision
from automata_api.core.tools.policy import ToolRisk as ToolRisk

ApprovalDecision = Literal["allow_once", "allow_for_run", "deny"]
InputDelivery = Literal["new", "steer", "queue"]

T = TypeVar("T")


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


class RunInputGate:
    """Serializes steering submission with the Run's finalization boundary.

    The database remains the source of truth for pending inputs. This gate
    only guarantees that a steering submission cannot race past the point at
    which the loop decides that it is safe to finish.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sealed = False

    async def accept(self, operation: Callable[[], Awaitable[T]]) -> T | None:
        async with self._lock:
            if self._sealed:
                return None
            return await operation()

    async def claim(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        seal: bool = False,
    ) -> T:
        async with self._lock:
            result = await operation()
            if seal:
                self._sealed = True
            return result

    @property
    def sealed(self) -> bool:
        return self._sealed


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
class PromptSubmission:
    delivery: InputDelivery
    input: dict[str, Any] | None = None
    run: dict[str, Any] | None = None
    message: dict[str, Any] | None = None
    idempotent: bool = False


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
