"""Run event replay.

Reconnect handling is subtle enough to deserve its own module: the client
asks for everything after a cursor, the sender buffers live events for that
run while replay is in flight, and the handover must deliver every event
exactly once in sequence order. None of that belongs in a WebSocket
protocol handler.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

from automata_api.core.runs.ports import RunStore
from automata_api.core.runs.state import EventCursorError, RunNotFoundError

REPLAY_PAGE_SIZE = 1000


class ReplaySender(Protocol):
    """Transport side of a replay, owned by the connection."""

    async def send_json(self, data: Any) -> None: ...

    async def begin_replay(self, run_id: str) -> None: ...

    async def send_replay_event(self, event: dict[str, Any]) -> None: ...

    async def abort_replay(self, run_id: str) -> None: ...

    async def finish_replay(
        self, run_id: str, watermark: int, completion: dict[str, Any]
    ) -> None: ...


@dataclass(frozen=True)
class ReplayOutcome:
    """What happened, so the caller can map it to a protocol message."""

    ok: bool
    cursor_error: bool = False
    run_missing: bool = False


class ReplayService:
    """Serves a run event backlog to one reconnecting connection."""

    def __init__(self, *, store: RunStore) -> None:
        self._store = store

    async def resume(
        self,
        *,
        sender: ReplaySender,
        session_id: str,
        run_id: str,
        after_sequence: int,
    ) -> ReplayOutcome:
        """Replay persisted events after ``after_sequence`` then go live.

        The sender's buffer is opened *before* the first page is read so an
        event that lands mid-replay is captured rather than lost; anything
        at or below the watermark is dropped by the sender to avoid a
        duplicate.
        """
        try:
            run = await asyncio.to_thread(
                self._store.get_session_run, session_id, run_id
            )
        except (ValueError, RunNotFoundError):
            return ReplayOutcome(ok=False, run_missing=True)

        watermark = int(run["last_sequence"])
        if after_sequence < 0 or after_sequence > watermark:
            return ReplayOutcome(ok=False, cursor_error=True)

        await sender.begin_replay(run_id)
        await sender.send_json(
            {
                "type": "run_resume_started",
                "session_id": session_id,
                "run_id": run_id,
                "after_sequence": after_sequence,
                "through_sequence": watermark,
            }
        )

        cursor = after_sequence
        while cursor < watermark:
            try:
                events = await asyncio.to_thread(
                    self._store.list_events,
                    run_id,
                    after_sequence=cursor,
                    through_sequence=watermark,
                    limit=REPLAY_PAGE_SIZE,
                )
            except EventCursorError:
                await sender.abort_replay(run_id)
                return ReplayOutcome(ok=False, cursor_error=True)
            if not events:
                break
            for event in events:
                await sender.send_replay_event(event)
            cursor = int(events[-1]["seq"])

        latest = await asyncio.to_thread(self._store.get_run, run_id)
        await sender.finish_replay(
            run_id,
            watermark,
            {
                "type": "run_resume_complete",
                "session_id": session_id,
                "run_id": run_id,
                "status": latest["status"],
                "last_sequence": latest["last_sequence"],
            },
        )
        return ReplayOutcome(ok=True)
