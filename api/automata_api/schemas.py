from typing import Any, Literal, NotRequired, TypedDict

from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    title: str | None = None
    working_directory: str | None = None
    backend: str | None = None


class UpdateSessionRequest(BaseModel):
    title: str


class McpGrantRequest(BaseModel):
    workspace: str
    connection: Literal["allow", "deny"] = "allow"
    trust: Literal["trusted", "untrusted"] = "untrusted"
    default_call_policy: Literal["allow", "deny", "prompt"] = "prompt"
    scope: Literal["global", "workspace"] = "workspace"
    tool_call_policies: dict[str, Literal["allow", "deny", "prompt"]] = Field(
        default_factory=dict
    )


class McpServerStatus(BaseModel):
    name: str
    provenance: Literal["explicit", "user", "workspace", "packaged"]
    fingerprint: str
    transport: Literal["stdio", "streamable_http"]
    granted: bool
    connection: Literal["allow", "deny"]
    trust: Literal["trusted", "untrusted"]
    default_call_policy: Literal["allow", "deny", "prompt"]


class SessionSummary(BaseModel):
    id: str
    title: str
    working_directory: str
    backend: str
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


class SkillSelectionPayload(TypedDict):
    name: NotRequired[str]
    path: NotRequired[str]


class SkillInterfaceRecord(BaseModel):
    display_name: str | None = None
    short_description: str | None = None
    icon_small: str | None = None
    icon_large: str | None = None
    brand_color: str | None = None
    default_prompt: str | None = None


class SkillToolDependencyRecord(BaseModel):
    type: str
    value: str | None = None
    description: str | None = None
    query: str | None = None
    server: str | None = None
    tool: str | None = None
    read_only: bool | None = None


class SkillDependenciesRecord(BaseModel):
    tools: list[SkillToolDependencyRecord] = Field(default_factory=list)


class SkillRecord(BaseModel):
    name: str
    description: str
    short_description: str | None = None
    path: str
    scope: Literal["repo", "user", "packaged", "extra", "plugin"]
    enabled: bool
    interface: SkillInterfaceRecord | None = None
    dependencies: SkillDependenciesRecord | None = None


class SkillErrorRecord(BaseModel):
    path: str
    message: str


class SkillsListResponse(BaseModel):
    workspace: str
    skills: list[SkillRecord]
    errors: list[SkillErrorRecord] = Field(default_factory=list)


class ChatPayload(TypedDict):
    type: str
    session_id: NotRequired[str]
    plan_id: NotRequired[str]
    prompt: NotRequired[str]
    mode: NotRequired[str]
    content: NotRequired[str]
    message: NotRequired[dict[str, Any] | str]
    skills: NotRequired[list[SkillSelectionPayload]]
