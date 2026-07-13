from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


CallPolicy = Literal["allow", "deny", "prompt"]
TrustLevel = Literal["trusted", "untrusted"]


class McpError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class McpDiscoveryLimits:
    total_timeout_seconds: float = 15.0
    max_pages: int = 32
    max_tools: int = 256
    max_tool_schema_bytes: int = 256_000
    max_description_chars: int = 8_000
    max_search_text_chars: int = 16_000


@dataclass(frozen=True)
class McpToolInfo:
    name: str
    title: str | None
    description: str | None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    annotations: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class McpListToolsPage:
    tools: tuple[McpToolInfo, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class McpCallResult:
    content: tuple[dict[str, Any], ...]
    structured_content: Any = None
    is_error: bool = False


@dataclass(frozen=True)
class McpToolMetadata:
    alias: str
    server_name: str
    server_fingerprint: str
    original_name: str
    title: str | None
    description: str | None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    read_only: bool
    destructive: bool
    idempotent: bool
    open_world: bool
    remote: bool
    credentialed: bool
    trusted_server: bool


@dataclass(frozen=True)
class McpPolicyDecision:
    action: CallPolicy
    reason: str
