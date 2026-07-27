from __future__ import annotations

from contextlib import asynccontextmanager, nullcontext
from dataclasses import dataclass
from typing import AsyncIterator

from automata_api.agent.backends.base import Backend
from automata_api.agent.execution.permissions import CompiledPermissionProfile
from automata_api.agent.execution.process import process_execution_scope
from automata_api.agent.mcp.client import McpSdkClientAdapter
from automata_api.agent.mcp.config import load_mcp_config
from automata_api.agent.mcp.manager import McpConnectionManager
from automata_api.agent.mcp.trust import McpTrustStore, server_fingerprint
from automata_api.agent.tools.mcp_provider import McpToolProvider
from automata_api.agent.tools.model import ToolDiscoveryContext
from automata_api.agent.tools.providers import BackendToolProvider
from automata_api.agent.tools.router import ToolRouter, ToolRouterBuilder
from automata_api.observability import observe_span


@dataclass(frozen=True)
class McpServerCandidate:
    name: str
    provenance: str
    fingerprint: str


@dataclass(frozen=True)
class McpRuntimeState:
    router: ToolRouter
    candidates: tuple[McpServerCandidate, ...]
    warnings: tuple[str, ...]


@asynccontextmanager
async def create_mcp_tool_runtime(
    *,
    backend: Backend,
    session_id: str,
    workspace: str,
    mode: str,
    trust_store: McpTrustStore | None = None,
    permission_profile: CompiledPermissionProfile | None = None,
    run_id: str | None = None,
    emit_event=None,
) -> AsyncIterator[McpRuntimeState]:
    config = load_mcp_config(workspace)
    store = trust_store or McpTrustStore()
    granted: list[tuple] = []
    candidates: list[McpServerCandidate] = []
    for definition in config.definitions:
        grant = store.grant_for(definition, workspace)
        if grant is None or grant.connection != "allow":
            candidates.append(
                McpServerCandidate(
                    name=definition.name,
                    provenance=definition.provenance,
                    fingerprint=server_fingerprint(definition, workspace),
                )
            )
            continue
        granted.append((definition, grant))

    definitions = tuple(definition for definition, _ in granted)
    scope = (
        process_execution_scope(
            run_id,
            "mcp-runtime",
            session_id=session_id,
            workspace=workspace,
            permission_profile=permission_profile,
            emit_event=emit_event,
        )
        if run_id is not None and permission_profile is not None
        else nullcontext()
    )
    with scope:
        async with McpConnectionManager(
            definitions,
            workspace,
            client_factory=lambda definition, resolved_workspace: McpSdkClientAdapter(
                definition,
                resolved_workspace,
                permission_profile=permission_profile,
            ),
        ) as manager:
            async with observe_span(
                "mcp.runtime.start",
                attributes={
                    "mode": mode,
                    "configured_server_count": len(config.definitions),
                    "granted_server_count": len(granted),
                    "candidate_server_count": len(candidates),
                },
            ):
                async_providers = (
                    (McpToolProvider(manager, tuple(granted)),)
                    if granted
                    else ()
                )
                router = await ToolRouterBuilder().build(
                    context=ToolDiscoveryContext(
                        session_id=session_id,
                        workspace=workspace,
                        backend=backend,
                        mode=mode,
                    ),
                    sync_providers=(BackendToolProvider(),),
                    async_providers=async_providers,
                )
            yield McpRuntimeState(
                router=router,
                candidates=tuple(candidates),
                warnings=config.warnings,
            )
