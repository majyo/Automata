"""Run states and business errors, independent of storage."""

from typing import Literal

RunKind = Literal["chat_act", "chat_plan", "plan_execution"]


RunMode = Literal["act", "plan"]


RunStatus = Literal[
    "queued",
    "running",
    "waiting_approval",
    "cancelling",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
]


NON_TERMINAL_STATUSES = (
    "queued",
    "running",
    "waiting_approval",
    "cancelling",
)


TERMINAL_STATUSES = ("completed", "failed", "cancelled", "interrupted")


class RunNotFoundError(ValueError):
    pass


class RunStateError(ValueError):
    pass


class SessionBusyError(ValueError):
    def __init__(self, run_id: str) -> None:
        super().__init__("This session already has an active run.")
        self.run_id = run_id


class RunNotSteerableError(ValueError):
    """The requested Run cannot accept a steering message."""


class InputConflictError(ValueError):
    """An idempotency key was reused with different input data."""


class PlanNotRetryableError(ValueError):
    pass


class EventCursorError(ValueError):
    pass
