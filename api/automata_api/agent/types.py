from collections.abc import Awaitable, Callable
from typing import Any, Literal, Protocol, TypedDict


EventEmitter = Callable[[dict[str, Any]], Awaitable[None]]


class AgentStepEvent(TypedDict):
    type: Literal["agent_step"]
    step: int
    mode: Literal["act", "plan"]
    message: str


class AgentTokenEvent(TypedDict):
    type: Literal["token"]
    content: str


class AgentToolCallEvent(TypedDict):
    type: Literal["tool_call"]
    tool: str
    arguments: str


class AgentToolResultEvent(TypedDict):
    type: Literal["tool_result"]
    tool: str
    success: bool
    content: str


class AgentFinalEvent(TypedDict):
    type: Literal["final"]
    content: str
    mode: Literal["act", "plan"]


AgentLoopEvent = (
    AgentStepEvent
    | AgentTokenEvent
    | AgentToolCallEvent
    | AgentToolResultEvent
    | AgentFinalEvent
    | dict[str, Any]
)


class AgentContextStore(Protocol):
    def get_recent_messages(self, session_id: str, limit: int) -> list[dict[str, Any]]:
        ...

    def get_messages_after_sequence(
        self, session_id: str, sequence: int
    ) -> list[dict[str, Any]]:
        ...

    def fetch_context_summary(self, session_id: str) -> dict[str, Any] | None:
        ...

    def upsert_context_summary(
        self, session_id: str, content: str, through_sequence: int
    ) -> dict[str, Any]:
        ...
