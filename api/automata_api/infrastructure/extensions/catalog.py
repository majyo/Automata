"""Persistent extension catalogs implementing the administration ports."""

from typing import Any

from automata_api.core.tools.router import ToolRouter
from automata_api.infrastructure.extensions.mcp.config import (
    load_mcp_config,
    transport_type,
)
from automata_api.infrastructure.extensions.mcp.lookup import mcp_config_lookup
from automata_api.infrastructure.extensions.mcp.trust import (
    McpTrustStore,
    create_grant,
    server_fingerprint,
)
from automata_api.infrastructure.extensions.skills.manager import get_skill_manager
from automata_api.infrastructure.workspace.backends.local import LocalBackend


class LocalMcpCatalog:
    def list_servers(self, workspace: str) -> list[dict[str, Any]]:
        store = McpTrustStore()
        return [
            self._status(definition, workspace, store)
            for definition in load_mcp_config(workspace).definitions
        ]

    def grant(self, server_name: str, workspace: str, **policy: Any) -> dict[str, Any]:
        definition = next(
            (
                item
                for item in load_mcp_config(workspace).definitions
                if item.name == server_name
            ),
            None,
        )
        if definition is None:
            raise KeyError(server_name)
        grant = create_grant(definition, workspace, **policy)
        store = McpTrustStore()
        store.save(grant)
        return self._status(definition, workspace, store)

    def revoke(self, fingerprint: str) -> bool:
        return McpTrustStore().revoke(fingerprint)

    @staticmethod
    def _status(definition, workspace, store) -> dict[str, Any]:
        grant = store.grant_for(definition, workspace)
        return dict(
            name=definition.name,
            provenance=definition.provenance,
            fingerprint=server_fingerprint(definition, workspace),
            transport=transport_type(definition),
            granted=grant is not None and grant.connection == "allow",
            connection=grant.connection if grant is not None else "deny",
            trust=grant.trust if grant is not None else "untrusted",
            default_call_policy=grant.default_call_policy
            if grant is not None
            else "prompt",
        )


class LocalSkillCatalog:
    mcp_lookup = mcp_config_lookup

    def skills_for_workspace(self, workspace: str, *, force_reload: bool = False):
        return get_skill_manager().skills_for_workspace(
            workspace, force_reload=force_reload
        )

    def set_enabled(self, workspace: str, skill_id: str, *, enabled: bool) -> None:
        get_skill_manager().set_enabled(workspace, skill_id, enabled=enabled)

    def router(self, workspace: str) -> ToolRouter:
        return ToolRouter.from_backend(LocalBackend(workspace), workspace=workspace)
