"""Authenticated endpoints used by the embedded workspace file panel."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query

from automata_api.core.workspace import (
    WorkspaceBrowseError,
    WorkspaceBrowser,
    WorkspaceDirectory,
    WorkspaceText,
)
from automata_api.transport.dependencies import workspace_browser
from automata_api.transport.schemas import WorkspaceDirectoryRecord, WorkspaceTextRecord

router = APIRouter(prefix="/workspace")

ERROR_STATUS = {
    "invalid_workspace": 422,
    "outside_workspace": 403,
    "not_found": 404,
    "not_directory": 422,
    "not_file": 422,
    "unsupported_text": 415,
    "permission_denied": 403,
    "io_error": 422,
}


@router.get("/directory", response_model=WorkspaceDirectoryRecord)
async def list_directory(
    workspace: str = Query(min_length=1),
    path: str = ".",
    browser: WorkspaceBrowser = Depends(workspace_browser),
) -> WorkspaceDirectory:
    try:
        return await asyncio.to_thread(browser.list_directory, workspace, path)
    except WorkspaceBrowseError as error:
        raise browse_http_error(error) from error


@router.get("/text", response_model=WorkspaceTextRecord)
async def read_text(
    workspace: str = Query(min_length=1),
    path: str = Query(min_length=1),
    browser: WorkspaceBrowser = Depends(workspace_browser),
) -> WorkspaceText:
    try:
        return await asyncio.to_thread(browser.read_text, workspace, path)
    except WorkspaceBrowseError as error:
        raise browse_http_error(error) from error


def browse_http_error(error: WorkspaceBrowseError) -> HTTPException:
    return HTTPException(
        status_code=ERROR_STATUS.get(error.code, 422),
        detail={"code": error.code, "message": str(error)},
    )
