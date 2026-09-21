from __future__ import annotations

import asyncio
from typing import Any, Protocol


class EventConnection(Protocol):
    @property
    def closed(self) -> bool: ...

    async def publish_json(self, data: Any) -> None: ...


class RunEventHub:
    def __init__(self) -> None:
        self._connections: set[EventConnection] = set()
        self._lock = asyncio.Lock()

    async def register(self, connection: EventConnection) -> None:
        async with self._lock:
            self._connections.add(connection)

    async def unregister(self, connection: EventConnection) -> None:
        async with self._lock:
            self._connections.discard(connection)

    async def broadcast(self, event: dict[str, Any]) -> None:
        async with self._lock:
            connections = tuple(self._connections)
        results = await asyncio.gather(
            *(connection.publish_json(event) for connection in connections),
            return_exceptions=True,
        )
        stale = {
            connection
            for connection, result in zip(connections, results, strict=True)
            if connection.closed or isinstance(result, Exception)
        }
        if stale:
            async with self._lock:
                self._connections.difference_update(stale)

    async def clear(self) -> None:
        async with self._lock:
            self._connections.clear()


run_event_hub = RunEventHub()
