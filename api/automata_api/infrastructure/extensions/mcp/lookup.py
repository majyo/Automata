"""MCP answers to the questions the agent's skill diagnostics asks.

The agent core defines the ``McpServerLookup`` port so it never imports MCP
types; this module supplies the real implementation from inside the
extension. Wiring happens at the composition boundary.
"""

from __future__ import annotations

from automata_api.infrastructure.extensions.mcp.config import load_mcp_config
from automata_api.infrastructure.extensions.mcp.trust import McpTrustStore


class McpConfigLookup:
    """Reads MCP configuration and grants from the workspace."""

    def __init__(self, store: McpTrustStore | None = None) -> None:
        self._store = store or McpTrustStore()

    def is_server_configured(self, server: str, workspace: str) -> bool:
        return self._definition(server, workspace) is not None

    def is_server_granted(self, server: str, workspace: str) -> bool:
        definition = self._definition(server, workspace)
        if definition is None:
            return False
        grant = self._store.grant_for(definition, workspace)
        return grant is not None and grant.connection == "allow"

    def _definition(self, server: str, workspace: str):
        config = load_mcp_config(workspace)
        return next(
            (candidate for candidate in config.definitions if candidate.name == server),
            None,
        )


mcp_config_lookup = McpConfigLookup()
