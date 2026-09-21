from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from automata_api.core.tools.management import McpCatalog
from automata_api.transport.dependencies import mcp_catalog
from automata_api.transport.schemas import McpGrantRequest, McpServerStatus

router = APIRouter()


@router.get("/mcp/servers", response_model=list[McpServerStatus])
async def list_mcp_servers(
    workspace: str, catalog: McpCatalog = Depends(mcp_catalog)
) -> list[McpServerStatus]:
    return [
        McpServerStatus(**item) for item in catalog.list_servers(_workspace(workspace))
    ]


@router.put("/mcp/grants/{server_name}", response_model=McpServerStatus)
async def put_mcp_grant(
    server_name: str,
    request: McpGrantRequest,
    catalog: McpCatalog = Depends(mcp_catalog),
) -> McpServerStatus:
    workspace = _workspace(request.workspace)
    try:
        result = catalog.grant(
            server_name,
            workspace,
            connection=request.connection,
            trust=request.trust,
            default_call_policy=request.default_call_policy,
            scope=request.scope,
            tool_call_policies=request.tool_call_policies,
        )
    except KeyError as error:
        raise HTTPException(
            status_code=404, detail="MCP server definition not found"
        ) from error
    return McpServerStatus(**result)


@router.delete("/mcp/grants/{fingerprint}", status_code=204)
async def revoke_mcp_grant(
    fingerprint: str, catalog: McpCatalog = Depends(mcp_catalog)
) -> None:
    if not catalog.revoke(fingerprint):
        raise HTTPException(status_code=404, detail="MCP grant not found")


def _workspace(value: str) -> str:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise HTTPException(
            status_code=422, detail="Workspace must be an existing directory"
        )
    return str(path)
