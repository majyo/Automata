"""Capability contracts for extension and sandbox administration."""

from typing import Any, Protocol

from automata_api.core.agent.skills.diagnostics import McpServerLookup
from automata_api.core.agent.skills.model import SkillLoadOutcome
from automata_api.core.tools.router import ToolRouter


class McpCatalog(Protocol):
    def list_servers(self, workspace: str) -> list[dict[str, Any]]: ...
    def grant(
        self, server_name: str, workspace: str, **policy: Any
    ) -> dict[str, Any]: ...
    def revoke(self, fingerprint: str) -> bool: ...


class SkillCatalog(Protocol):
    @property
    def mcp_lookup(self) -> McpServerLookup: ...
    def skills_for_workspace(
        self, workspace: str, *, force_reload: bool = False
    ) -> SkillLoadOutcome: ...
    def set_enabled(self, workspace: str, skill_id: str, *, enabled: bool) -> None: ...
    def router(self, workspace: str) -> ToolRouter: ...


class SandboxAdministration(Protocol):
    def status(self) -> dict[str, Any]: ...
    async def prepare(self, workspace: str) -> dict[str, Any]: ...
