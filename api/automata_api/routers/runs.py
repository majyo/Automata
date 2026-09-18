from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from automata_api.repositories.runs import (
    EventCursorError,
    PlanNotRetryableError,
    RunNotFoundError,
    RunStore,
)
from automata_api.schemas import PlanAttemptRecord, RunRecord
from automata_api.transport.dependencies import run_store

router = APIRouter()


@router.get("/runs", response_model=list[RunRecord])
async def list_runs(
    status: Literal["non_terminal"] | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    store: RunStore = Depends(run_store),
) -> list[dict[str, Any]]:
    return await repository_call(
        store.list_runs,
        non_terminal_only=status == "non_terminal",
        limit=limit,
    )


@router.get("/sessions/{session_id}/runs", response_model=list[RunRecord])
async def list_session_runs(
    session_id: str,
    status: Literal["non_terminal"] | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    store: RunStore = Depends(run_store),
) -> list[dict[str, Any]]:
    try:
        return await repository_call(
            store.list_runs,
            session_id=session_id,
            non_terminal_only=status == "non_terminal",
            limit=limit,
        )
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get(
    "/sessions/{session_id}/runs/{run_id}",
    response_model=RunRecord,
)
async def get_session_run(
    session_id: str,
    run_id: str,
    store: RunStore = Depends(run_store),
) -> dict[str, Any]:
    try:
        return await repository_call(
            store.get_session_run,
            session_id,
            run_id,
        )
    except (ValueError, RunNotFoundError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/sessions/{session_id}/runs/{run_id}/events")
async def list_run_events(
    session_id: str,
    run_id: str,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    store: RunStore = Depends(run_store),
) -> list[dict[str, Any]]:
    try:
        await repository_call(
            store.get_session_run,
            session_id,
            run_id,
        )
        return await repository_call(
            store.list_events,
            run_id,
            after_sequence=after_sequence,
            limit=limit,
        )
    except EventCursorError as error:
        raise HTTPException(
            status_code=400,
            detail={"code": "event_cursor_invalid", "message": str(error)},
        ) from error
    except (ValueError, RunNotFoundError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get(
    "/sessions/{session_id}/plans/{plan_id}/attempts",
    response_model=list[PlanAttemptRecord],
)
async def list_plan_attempts(
    session_id: str,
    plan_id: str,
    store: RunStore = Depends(run_store),
) -> list[dict[str, Any]]:
    try:
        return await repository_call(
            store.list_plan_attempts,
            session_id,
            plan_id,
        )
    except (ValueError, PlanNotRetryableError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


async def repository_call(function, /, *args, **kwargs):
    """Run a blocking store call off the event loop.

    Every storage port method owns its transaction; this only moves the
    synchronous SQLite work to a thread.
    """
    return await asyncio.to_thread(function, *args, **kwargs)
