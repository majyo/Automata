from typing import Any, Literal, NotRequired, TypedDict

from pydantic import BaseModel, Field, model_validator

from automata_api.agent.execution.permissions import PermissionPreset


class CreateSessionRequest(BaseModel):
    title: str | None = None
    working_directory: str | None = None
    backend: str | None = None
    permission_preset: PermissionPreset = "default"


class UpdateSessionRequest(BaseModel):
    title: str | None = None
    permission_preset: PermissionPreset | None = None

    @model_validator(mode="after")
    def require_update(self) -> "UpdateSessionRequest":
        if self.title is None and self.permission_preset is None:
            raise ValueError("At least one session field must be provided.")
        return self


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
    permission_preset: PermissionPreset
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
    plan_status: Literal[
        "pending", "executing", "failed", "executed", "superseded"
    ] | None = None


class RunRecord(BaseModel):
    id: str
    session_id: str
    kind: Literal["chat_act", "chat_plan", "plan_execution"]
    mode: Literal["act", "plan"]
    permission_preset: PermissionPreset
    status: Literal[
        "queued",
        "running",
        "waiting_approval",
        "cancelling",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
    ]
    request_message_id: str | None = None
    response_message_id: str | None = None
    plan_id: str | None = None
    owner_instance_id: str
    last_sequence: int
    error_code: str | None = None
    public_error: str | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    heartbeat_at: str | None = None


class PlanAttemptRecord(BaseModel):
    id: str
    plan_id: str
    run_id: str
    attempt_no: int
    request_id: str
    created_at: str
    status: str
    error_code: str | None = None
    public_error: str | None = None
    finished_at: str | None = None


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


class SkillDependencyDiagnosticRecord(BaseModel):
    dependency_type: str
    status: Literal[
        "available",
        "deferred",
        "not_granted",
        "not_found",
        "unknown",
    ]
    message: str
    value: str | None = None
    query: str | None = None
    server: str | None = None
    tool: str | None = None


class SkillDependenciesRecord(BaseModel):
    tools: list[SkillToolDependencyRecord] = Field(default_factory=list)


class SkillRecord(BaseModel):
    skill_id: str
    name: str
    description: str
    short_description: str | None = None
    path: str
    scope: Literal["repo", "user", "packaged", "extra", "plugin"]
    enabled: bool
    root_id: str
    relative_dir: str
    fingerprint: str
    interface: SkillInterfaceRecord | None = None
    dependencies: SkillDependenciesRecord | None = None
    diagnostics: list[SkillDependencyDiagnosticRecord] = Field(default_factory=list)


class SkillErrorRecord(BaseModel):
    path: str
    message: str
    severity: Literal["warning", "error"] = "error"


class SkillsListResponse(BaseModel):
    workspace: str
    skills: list[SkillRecord]
    errors: list[SkillErrorRecord] = Field(default_factory=list)


class SkillEnabledRequest(BaseModel):
    workspace: str
    enabled: bool


class SkillDiagnosticsResponse(BaseModel):
    skill_id: str
    diagnostics: list[SkillDependencyDiagnosticRecord] = Field(default_factory=list)


class ChatPayload(TypedDict):
    type: str
    session_id: NotRequired[str]
    plan_id: NotRequired[str]
    prompt: NotRequired[str]
    mode: NotRequired[str]
    content: NotRequired[str]
    message: NotRequired[dict[str, Any] | str]
    skills: NotRequired[list[SkillSelectionPayload]]
