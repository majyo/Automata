import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from automata_api.schemas import (
    CreateSessionRequest,
    MessageRecord,
    SessionSummary,
    UpdateSessionRequest,
)
from automata_api.sessions.domain import (
    InvalidBackendError,
    InvalidPermissionPresetError,
    InvalidWorkingDirectoryError,
    SessionHasActiveRunError,
    SessionNotFoundError,
)
from automata_api.sessions.ports import ConversationStore, SessionStore
from automata_api.transport.dependencies import conversation_store, session_store

router = APIRouter()


@router.get("/sessions", response_model=list[SessionSummary])
async def list_sessions(
    store: SessionStore = Depends(session_store),
) -> list[dict[str, Any]]:
    return await store_call(store.list_sessions)


@router.post("/sessions", response_model=SessionSummary, status_code=201)
async def create_session(
    request: CreateSessionRequest,
    store: SessionStore = Depends(session_store),
) -> dict[str, Any]:
    try:
        return await store_call(
            store.create_session,
            request.title,
            request.working_directory,
            request.backend,
            request.permission_preset,
        )
    except InvalidBackendError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except InvalidWorkingDirectoryError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except InvalidPermissionPresetError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.patch("/sessions/{session_id}", response_model=SessionSummary)
async def update_session(
    session_id: str,
    request: UpdateSessionRequest,
    store: SessionStore = Depends(session_store),
) -> dict[str, Any]:
    try:
        return await store_call(
            store.update_session,
            session_id,
            title=request.title,
            permission_preset=request.permission_preset,
        )
    except SessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except InvalidPermissionPresetError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    store: SessionStore = Depends(session_store),
) -> None:
    try:
        await store_call(store.delete_session, session_id)
    except SessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except SessionHasActiveRunError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "session_busy",
                "run_id": error.run_id,
                "message": str(error),
            },
        ) from error


@router.get("/sessions/{session_id}/messages", response_model=list[MessageRecord])
async def list_messages(
    session_id: str,
    store: ConversationStore = Depends(conversation_store),
) -> list[dict[str, Any]]:
    try:
        return await store_call(store.list_messages, session_id)
    except SessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


async def store_call(function, /, *args, **kwargs):
    """Run a blocking store call off the event loop.

    Every storage port method owns its transaction; this only moves the
    synchronous SQLite work to a thread.
    """
    return await asyncio.to_thread(function, *args, **kwargs)
