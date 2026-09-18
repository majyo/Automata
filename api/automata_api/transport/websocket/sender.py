"""Serialized, replay-aware WebSocket sender.

One connection owns one sender. It exists to serialize writes to a single
socket and to buffer live run events while a replay is in flight, so the
handover delivers every event exactly once and in sequence order.

This is connection-scoped state: it is created for a socket and released
with it. It never cancels a Run.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket


class SerializedWebSocketSender:
    """Writes to one WebSocket under a lock, buffering replayed runs.

    ``publish_json`` is the path run events take: while a run is being
    replayed, its events are held instead of sent, then ``finish_replay``
    flushes only those above the watermark. That is what prevents a live
    event from overtaking the backlog or being delivered twice.
    """

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._lock = asyncio.Lock()
        self._closed = False
        self._replay_buffers: dict[str, list[dict[str, Any]]] = {}

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """Mark the sender closed and release buffered events."""
        self._closed = True
        self._replay_buffers.clear()

    async def send_json(self, data: Any) -> None:
        async with self._lock:
            await self._send_locked(data)

    async def publish_json(self, data: Any) -> None:
        async with self._lock:
            if isinstance(data, dict):
                run_id = data.get("run_id")
                if isinstance(run_id, str) and run_id in self._replay_buffers:
                    self._replay_buffers[run_id].append(data)
                    return
            await self._send_locked(data)

    async def begin_replay(self, run_id: str) -> None:
        async with self._lock:
            self._replay_buffers.setdefault(run_id, [])

    async def send_replay_event(self, event: dict[str, Any]) -> None:
        async with self._lock:
            await self._send_locked(event)

    async def abort_replay(self, run_id: str) -> None:
        async with self._lock:
            self._replay_buffers.pop(run_id, None)

    async def finish_replay(
        self,
        run_id: str,
        watermark: int,
        completion: dict[str, Any],
    ) -> None:
        """Flush buffered live events above the watermark, then complete."""
        async with self._lock:
            buffered = self._replay_buffers.pop(run_id, [])
            for event in sorted(buffered, key=lambda item: int(item.get("seq", 0))):
                if int(event.get("seq", 0)) > watermark:
                    await self._send_locked(event)
            await self._send_locked(completion)

    async def _send_locked(self, data: Any) -> None:
        if not self._closed:
            await self._websocket.send_json(data)
