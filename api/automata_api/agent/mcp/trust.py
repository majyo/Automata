from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

from automata_api.agent.mcp.config import (
    McpServerDefinition,
    McpStdioTransportDefinition,
    McpStreamableHttpTransportDefinition,
)
from automata_api.agent.mcp.schema import CallPolicy, TrustLevel
from automata_api.config import get_database_config


GrantScope = Literal["global", "workspace"]


@dataclass(frozen=True)
class McpServerGrant:
    server_fingerprint: str
    connection: Literal["allow", "deny"] = "deny"
    trust: TrustLevel = "untrusted"
    default_call_policy: CallPolicy = "prompt"
    tool_call_policies: Mapping[str, CallPolicy] = field(default_factory=dict)
    scope: GrantScope = "workspace"
    workspace: str | None = None


def server_fingerprint(definition: McpServerDefinition, workspace: str) -> str:
    normalized_workspace = str(Path(workspace).expanduser().resolve())
    payload = {
        "workspace": normalized_workspace,
        **_transport_fingerprint_payload(definition),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _transport_fingerprint_payload(definition: McpServerDefinition) -> dict[str, Any]:
    transport = definition.transport
    if isinstance(transport, McpStdioTransportDefinition):
        return {
            "transport": "stdio",
            "command": transport.command,
            "args": list(transport.args),
            "cwd": transport.cwd,
            "env": sorted(transport.env.items()),
        }
    if isinstance(transport, McpStreamableHttpTransportDefinition):
        return {
            "transport": "streamable_http",
            "url": transport.url,
            "headers": sorted(transport.headers.items()),
        }
    raise TypeError(f"Unsupported MCP transport: {transport!r}")


class McpTrustStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (get_database_config().path.parent / "mcp-grants.json")

    def load(self) -> tuple[McpServerGrant, ...]:
        if not self.path.exists():
            return ()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ()
        raw_grants = payload.get("grants") if isinstance(payload, dict) else None
        if not isinstance(raw_grants, list):
            return ()
        grants: list[McpServerGrant] = []
        for raw_grant in raw_grants:
            try:
                grants.append(_parse_grant(raw_grant))
            except (TypeError, ValueError):
                continue
        return tuple(grants)

    def grant_for(
        self, definition: McpServerDefinition, workspace: str
    ) -> McpServerGrant | None:
        fingerprint = server_fingerprint(definition, workspace)
        normalized_workspace = str(Path(workspace).expanduser().resolve())
        for grant in self.load():
            if grant.server_fingerprint != fingerprint:
                continue
            if grant.scope == "global" or grant.workspace == normalized_workspace:
                return grant
        return None

    def save(self, grant: McpServerGrant) -> None:
        grants = {
            existing.server_fingerprint: existing for existing in self.load()
        }
        grants[grant.server_fingerprint] = grant
        self._write(tuple(grants.values()))

    def revoke(self, fingerprint: str) -> bool:
        grants = [
            grant
            for grant in self.load()
            if grant.server_fingerprint != fingerprint
        ]
        if len(grants) == len(self.load()):
            return False
        self._write(tuple(grants))
        return True

    def _write(self, grants: tuple[McpServerGrant, ...]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "grants": [asdict(grant) for grant in grants],
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


def create_grant(
    definition: McpServerDefinition,
    workspace: str,
    *,
    connection: Literal["allow", "deny"],
    trust: TrustLevel,
    default_call_policy: CallPolicy,
    scope: GrantScope = "workspace",
    tool_call_policies: Mapping[str, CallPolicy] | None = None,
) -> McpServerGrant:
    normalized_workspace = str(Path(workspace).expanduser().resolve())
    return McpServerGrant(
        server_fingerprint=server_fingerprint(definition, workspace),
        connection=connection,
        trust=trust,
        default_call_policy=default_call_policy,
        tool_call_policies=dict(tool_call_policies or {}),
        scope=scope,
        workspace=normalized_workspace if scope == "workspace" else None,
    )


def _parse_grant(value: Any) -> McpServerGrant:
    if not isinstance(value, dict):
        raise TypeError("grant must be an object")
    fingerprint = value.get("server_fingerprint")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise ValueError("invalid server fingerprint")
    connection = value.get("connection", "deny")
    trust = value.get("trust", "untrusted")
    policy = value.get("default_call_policy", "prompt")
    scope = value.get("scope", "workspace")
    if connection not in {"allow", "deny"}:
        raise ValueError("invalid connection grant")
    if trust not in {"trusted", "untrusted"}:
        raise ValueError("invalid trust level")
    if policy not in {"allow", "deny", "prompt"}:
        raise ValueError("invalid call policy")
    if scope not in {"global", "workspace"}:
        raise ValueError("invalid grant scope")
    raw_tool_policies = value.get("tool_call_policies", {})
    if not isinstance(raw_tool_policies, dict) or not all(
        isinstance(name, str) and item in {"allow", "deny", "prompt"}
        for name, item in raw_tool_policies.items()
    ):
        raise ValueError("invalid tool call policies")
    workspace = value.get("workspace")
    if scope == "workspace" and not isinstance(workspace, str):
        raise ValueError("workspace grant requires a workspace")
    return McpServerGrant(
        server_fingerprint=fingerprint,
        connection=connection,
        trust=trust,
        default_call_policy=policy,
        tool_call_policies=dict(raw_tool_policies),
        scope=scope,
        workspace=workspace if isinstance(workspace, str) else None,
    )
