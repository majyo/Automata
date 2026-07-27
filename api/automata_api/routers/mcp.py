from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from automata_api.agent.mcp.config import load_mcp_config, transport_type
from automata_api.agent.mcp.trust import (
    McpTrustStore,
    create_grant,
    server_fingerprint,
)
from automata_api.schemas import McpGrantRequest, McpServerStatus

router = APIRouter()


@router.get("/mcp/servers", response_model=list[McpServerStatus])
async def list_mcp_servers(workspace: str) -> list[McpServerStatus]:
    normalized_workspace = _workspace(workspace)
    result = load_mcp_config(normalized_workspace)
    store = McpTrustStore()
    return [
        _server_status(definition, normalized_workspace, store)
        for definition in result.definitions
    ]


@router.put("/mcp/grants/{server_name}", response_model=McpServerStatus)
async def put_mcp_grant(
    server_name: str,
    request: McpGrantRequest,
) -> McpServerStatus:
    workspace = _workspace(request.workspace)
    result = load_mcp_config(workspace)
    definition = next(
        (
            item
            for item in result.definitions
            if item.name == server_name
        ),
        None,
    )
    if definition is None:
        raise HTTPException(status_code=404, detail="MCP server definition not found")
    grant = create_grant(
        definition,
        workspace,
        connection=request.connection,
        trust=request.trust,
        default_call_policy=request.default_call_policy,
        scope=request.scope,
        tool_call_policies=request.tool_call_policies,
    )
    store = McpTrustStore()
    store.save(grant)
    return _server_status(definition, workspace, store)


@router.delete("/mcp/grants/{fingerprint}", status_code=204)
async def revoke_mcp_grant(fingerprint: str) -> None:
    if not McpTrustStore().revoke(fingerprint):
        raise HTTPException(status_code=404, detail="MCP grant not found")


def _server_status(definition, workspace: str, store: McpTrustStore) -> McpServerStatus:
    grant = store.grant_for(definition, workspace)
    return McpServerStatus(
        name=definition.name,
        provenance=definition.provenance,
        fingerprint=server_fingerprint(definition, workspace),
        transport=transport_type(definition),
        granted=grant is not None and grant.connection == "allow",
        connection=grant.connection if grant is not None else "deny",
        trust=grant.trust if grant is not None else "untrusted",
        default_call_policy=(
            grant.default_call_policy if grant is not None else "prompt"
        ),
    )


def _workspace(value: str) -> str:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise HTTPException(status_code=422, detail="Workspace must be an existing directory")
    return str(path)
