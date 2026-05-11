from typing import Any, Literal, NotRequired, TypedDict

from pydantic import BaseModel


class CreateSessionRequest(BaseModel):
    title: str | None = None


class UpdateSessionRequest(BaseModel):
    title: str


class SessionSummary(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int


class MessageRecord(BaseModel):
    id: str
    session_id: str
    role: Literal["user", "agent"]
    content: str
    sequence: int
    created_at: str


class ChatPayload(TypedDict):
    type: str
    session_id: NotRequired[str]
    prompt: NotRequired[str]
    content: NotRequired[str]
    message: NotRequired[dict[str, Any] | str]
