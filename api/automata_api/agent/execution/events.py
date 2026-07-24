from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from automata_api.agent.execution.event_hub import RunEventHub, run_event_hub
from automata_api.observability import (
    emit_profile_event,
    get_observability_manager,
)
from automata_api.repositories import runs as run_repository


class DurableRunEventSink:
    def __init__(
        self,
        *,
        run_id: str,
        hub: RunEventHub = run_event_hub,
    ) -> None:
        self.run_id = run_id
        self._hub = hub
        self._lock = asyncio.Lock()
        self._token_parts: list[str] = []
        self._token_chars = 0
        self._token_timer: asyncio.Task[None] | None = None
        self._closed = False
        self._tool_output_chars = 0
        self._first_token_queued_ns: int | None = None
        self._persist_count = 0
        self._persist_duration_ns = 0
        self._broadcast_duration_ns = 0
        self._max_token_buffer_delay_ns = 0
        self._token_chunk_chars = read_positive_int(
            "AUTOMATA_RUN_EVENT_TOKEN_CHUNK_CHARS", 4096
        )
        self._max_payload_bytes = read_positive_int(
            "AUTOMATA_RUN_EVENT_MAX_BYTES", 65_536
        )
        self._max_tool_output_chars = read_positive_int(
            "AUTOMATA_RUN_TOOL_OUTPUT_MAX_CHARS",
            1_000_000,
        )

    async def send_json(self, data: Any) -> None:
        if self._closed:
            return
        if not isinstance(data, dict):
            raise TypeError("Run events must be JSON objects.")
        payload = redact_event(data)
        if payload.get("type") == "tool_output_delta":
            content = payload.get("content")
            if not isinstance(content, str) or not content:
                return
            remaining = self._max_tool_output_chars - self._tool_output_chars
            if remaining <= 0:
                return
            bounded = content[:remaining]
            self._tool_output_chars += len(bounded)
            payload = {
                **payload,
                "content": bounded,
                "truncated": payload.get("truncated") is True
                or len(bounded) < len(content),
            }
        if payload.get("type") == "token":
            content = payload.get("content")
            if isinstance(content, str) and content:
                await self._queue_token(content)
            return

        async with self._lock:
            await self._flush_tokens_locked()
            await self._persist_and_broadcast_locked(payload)

    async def flush(self) -> None:
        async with self._lock:
            await self._flush_tokens_locked()

    async def close(self) -> None:
        async with self._lock:
            await self._flush_tokens_locked()
            self._closed = True
            timer = self._token_timer
            self._token_timer = None
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)
        get_observability_manager().emit(
            {
                "record_type": "event_sink_summary",
                "run_id": self.run_id,
                "attributes": {
                    "persist_count": self._persist_count,
                    "persist_duration_ns": self._persist_duration_ns,
                    "broadcast_duration_ns": self._broadcast_duration_ns,
                    "max_token_buffer_delay_ns": (
                        self._max_token_buffer_delay_ns
                    ),
                },
            },
            critical=True,
        )

    async def broadcast_persisted(self, event: dict[str, Any]) -> None:
        await self._hub.broadcast(event)

    async def _queue_token(self, content: str) -> None:
        async with self._lock:
            if not self._token_parts:
                self._first_token_queued_ns = time.monotonic_ns()
            self._token_parts.append(content)
            self._token_chars += len(content)
            if self._token_chars >= self._token_chunk_chars:
                await self._flush_tokens_locked()
                return
            if self._token_timer is None or self._token_timer.done():
                self._token_timer = asyncio.create_task(self._flush_tokens_after_delay())

    async def _flush_tokens_after_delay(self) -> None:
        try:
            await asyncio.sleep(0.1)
            async with self._lock:
                await self._flush_tokens_locked()
        except asyncio.CancelledError:
            return

    async def _flush_tokens_locked(self) -> None:
        if not self._token_parts:
            return
        content = "".join(self._token_parts)
        if self._first_token_queued_ns is not None:
            self._max_token_buffer_delay_ns = max(
                self._max_token_buffer_delay_ns,
                time.monotonic_ns() - self._first_token_queued_ns,
            )
        self._first_token_queued_ns = None
        self._token_parts.clear()
        self._token_chars = 0
        timer = self._token_timer
        self._token_timer = None
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()
        await self._persist_and_broadcast_locked(
            {"type": "token", "content": content}
        )

    async def _persist_and_broadcast_locked(
        self, payload: dict[str, Any]
    ) -> None:
        event_type = str(payload.get("type", ""))
        if event_type == "tool_approval_required":
            await asyncio.to_thread(
                run_repository.transition_run,
                self.run_id,
                expected=("running",),
                target="waiting_approval",
            )
        elif event_type == "tool_approval_resolved":
            try:
                await asyncio.to_thread(
                    run_repository.transition_run,
                    self.run_id,
                    expected=("waiting_approval",),
                    target="running",
                )
            except run_repository.RunStateError:
                pass

        persist_started_ns = time.monotonic_ns()
        event = await asyncio.to_thread(
            run_repository.append_event,
            self.run_id,
            payload,
            max_payload_bytes=self._max_payload_bytes,
        )
        persist_duration_ns = time.monotonic_ns() - persist_started_ns
        self._persist_count += 1
        self._persist_duration_ns += persist_duration_ns
        broadcast_started_ns = time.monotonic_ns()
        await self._hub.broadcast(event)
        broadcast_duration_ns = time.monotonic_ns() - broadcast_started_ns
        self._broadcast_duration_ns += broadcast_duration_ns
        emit_profile_event(
            "runtime.event.persist_and_broadcast",
            {
                "event_type": event_type,
                "payload_bytes": len(str(payload).encode("utf-8")),
                "persist_duration_ns": persist_duration_ns,
                "broadcast_duration_ns": broadcast_duration_ns,
            },
        )


def read_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(1, value)


def redact_event(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): redact_value(str(key), value)
        for key, value in payload.items()
        if str(key).lower() not in {"authorization", "api_token", "access_token"}
    }


def redact_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if any(
        marker in lowered
        for marker in ("authorization", "api_token", "access_token", "password", "secret")
    ):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(child): redact_value(str(child), item) for child, item in value.items()}
    if isinstance(value, list):
        return [redact_value(key, item) for item in value]
    return value
