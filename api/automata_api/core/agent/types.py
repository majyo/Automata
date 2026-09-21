from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypeAlias, TypedDict

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
    tool_call_id: str
    tool: str
    arguments: str


class AgentToolResultEvent(TypedDict):
    type: Literal["tool_result"]
    tool_call_id: str
    tool: str
    success: bool
    content: str


class AgentFinalEvent(TypedDict):
    type: Literal["final"]
    content: str
    mode: Literal["act", "plan"]


AgentLoopEvent: TypeAlias = (
    AgentStepEvent
    | AgentTokenEvent
    | AgentToolCallEvent
    | AgentToolResultEvent
    | AgentFinalEvent
    | dict[str, Any]
)
