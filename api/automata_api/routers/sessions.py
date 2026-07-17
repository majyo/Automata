import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException

from automata_api.repositories import sessions as session_repository
from automata_api.repositories.sessions import (
    InvalidBackendError,
    InvalidWorkingDirectoryError,
    SessionHasActiveRunError,
    SessionNotFoundError,
)
from automata_api.schemas import (
    CreateSessionRequest,
    MessageRecord,
    SessionSummary,
    UpdateSessionRequest,
)


router = APIRouter()


@router.get("/sessions", response_model=list[SessionSummary])
async def list_sessions() -> list[dict[str, Any]]:
    return await run_repository_call(session_repository.list_sessions)


@router.post("/sessions", response_model=SessionSummary, status_code=201)
async def create_session(request: CreateSessionRequest) -> dict[str, Any]:
    try:
        return await run_repository_call(
            session_repository.create_session,
            request.title,
            request.working_directory,
            request.backend,
        )
    except InvalidBackendError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except InvalidWorkingDirectoryError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.patch("/sessions/{session_id}", response_model=SessionSummary)
async def update_session(
    session_id: str, request: UpdateSessionRequest
) -> dict[str, Any]:
    try:
        return await run_repository_call(
            session_repository.update_session, session_id, request.title
        )
    except SessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str) -> None:
    try:
        await run_repository_call(session_repository.delete_session, session_id)
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
async def list_messages(session_id: str) -> list[dict[str, Any]]:
    try:
        return await run_repository_call(session_repository.list_messages, session_id)
    except SessionNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


async def run_repository_call(function, /, *args, **kwargs):
    return await asyncio.to_thread(function, *args, **kwargs)
