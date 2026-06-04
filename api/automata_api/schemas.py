from typing import Any, Literal, NotRequired, TypedDict

from pydantic import BaseModel


class CreateSessionRequest(BaseModel):
    title: str | None = None
    working_directory: str | None = None


class UpdateSessionRequest(BaseModel):
    title: str


class SessionSummary(BaseModel):
    id: str
    title: str
    working_directory: str
    created_at: str
    updated_at: str
    message_count: int


class MessageRecord(BaseModel):
    id: str
    session_id: str
    role: Literal["user", "agent", "tool"]
    kind: Literal["message", "tool_run"] = "message"
    content: str
    metadata: dict[str, Any] | None = None
    sequence: int
    created_at: str
    plan_id: str | None = None
    plan_status: Literal["pending", "approved", "executed", "superseded"] | None = None


class ChatPayload(TypedDict):
    type: str
    session_id: NotRequired[str]
    plan_id: NotRequired[str]
    prompt: NotRequired[str]
    mode: NotRequired[str]
    content: NotRequired[str]
    message: NotRequired[dict[str, Any] | str]
