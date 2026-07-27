from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from automata_api.agent.execution.approval import ApprovalResolutionError
from automata_api.agent.execution.coordinator import (
    RunHandle,
    run_coordinator,
)
from automata_api.agent.execution.event_hub import run_event_hub
from automata_api.agent.execution.model import RunOutcome
from automata_api.agent.status import agent_ready_message
from automata_api.config import get_api_config
from automata_api.repositories import runs as run_repository
from automata_api.repositories.sessions import (
    save_context_message,
    session_exists,
)
from automata_api.security import authenticate_websocket
from automata_api.services.chat import (
    receive_payload,
    run_repository_call,
    stream_agent_reply,
    stream_approved_plan_reply,
    stream_plan_reply,
)


class SerializedWebSocketSender:
    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._lock = asyncio.Lock()
        self._closed = False
        self._replay_buffers: dict[str, list[dict[str, Any]]] = {}

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
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
        async with self._lock:
            buffered = self._replay_buffers.pop(run_id, [])
            for event in sorted(buffered, key=lambda item: int(item.get("seq", 0))):
                if int(event.get("seq", 0)) > watermark:
                    await self._send_locked(event)
            await self._send_locked(completion)

    async def _send_locked(self, data: Any) -> None:
        if not self._closed:
            await self._websocket.send_json(data)


class AgentConnection:
    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.sender = SerializedWebSocketSender(websocket)
        self.connection_id = uuid.uuid4().hex

    async def serve(self) -> None:
        authenticated = await authenticate_websocket(
            self.websocket,
            allowed_origins=get_api_config().cors_origins,
        )
        if not authenticated:
            return

        await run_event_hub.register(self.sender)
        try:
            active_runs = await run_repository_call(
                run_repository.list_runs,
                non_terminal_only=True,
                limit=500,
            )
            await self.sender.send_json(
                {
                    "type": "ready",
                    "message": agent_ready_message(),
                    "active_runs": active_runs,
                }
            )
            while True:
                payload = await receive_payload(self.websocket)
                await self._handle_payload(payload)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            await run_event_hub.unregister(self.sender)
            self.sender.close()

    async def _handle_payload(self, payload: Mapping[str, Any]) -> None:
        payload_type = payload.get("type")
        if payload_type == "tool_approval_response":
            await self._resolve_approval(payload)
            return
        if payload_type == "cancel_run":
            await self._handle_cancel(payload)
            return
        if payload_type == "resume_run":
            await self._resume_run(payload)
            return
        if payload_type not in {
            "prompt",
            "approve_plan",
            "retry_plan",
        }:
            await self.sender.send_json(
                {"type": "error", "message": "Unsupported message type"}
            )
            return

        session_id = str(payload.get("session_id", "")).strip()
        if not session_id:
            await self.sender.send_json(
                {"type": "error", "message": "Missing session_id"}
            )
            return
        if not await run_repository_call(session_exists, session_id):
            await self.sender.send_json(
                {"type": "error", "message": "Session not found"}
            )
            return

        if payload_type == "prompt":
            await self._start_prompt(session_id, payload)
            return
        await self._start_plan_execution(
            session_id,
            payload,
            retry=payload_type == "retry_plan",
        )

    async def _start_prompt(
        self, session_id: str, payload: Mapping[str, Any]
    ) -> None:
        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            await self.sender.send_json(
                {"type": "error", "message": "Missing prompt"}
            )
            return
        mode = "plan" if str(payload.get("mode", "")).strip() == "plan" else "act"

        async def execute(
            run: RunHandle, user_message: dict[str, Any]
        ) -> RunOutcome:
            await run_repository_call(
                save_context_message,
                session_id=run.session_id,
                message={"role": "user", "content": prompt},
            )
            if mode == "plan":
                return await stream_plan_reply(
                    run.event_sink,
                    run.session_id,
                    prompt,
                    str(user_message["id"]),
                    run.run_id,
                    run.cancellation,
                    run.approval_broker,
                    run.permission_preset,
                    run.permission_profile,
                    payload.get("skills"),
                )
            return await stream_agent_reply(
                run.event_sink,
                run.session_id,
                prompt,
                run.run_id,
                run.cancellation,
                run.approval_broker,
                run.permission_preset,
                run.permission_profile,
                payload.get("skills"),
            )

        try:
            await run_coordinator.start_prompt(
                session_id=session_id,
                prompt=prompt,
                mode=mode,
                execute=execute,
            )
        except run_repository.SessionBusyError as error:
            await self._send_session_busy(session_id, error.run_id)

    async def _start_plan_execution(
        self,
        session_id: str,
        payload: Mapping[str, Any],
        *,
        retry: bool,
    ) -> None:
        plan_id = str(payload.get("plan_id", "")).strip()
        if not plan_id:
            await self.sender.send_json(
                {"type": "plan_error", "message": "Missing plan_id"}
            )
            return
        if retry and payload.get("confirm_possible_duplicate_side_effects") is not True:
            await self.sender.send_json(
                {
                    "type": "plan_error",
                    "code": "duplicate_side_effect_confirmation_required",
                    "session_id": session_id,
                    "plan_id": plan_id,
                    "message": (
                        "Retry requires confirmation of possible duplicate side effects."
                    ),
                }
            )
            return
        request_id = str(payload.get("request_id", "")).strip() or uuid.uuid4().hex

        async def execute(
            run: RunHandle, plan: dict[str, Any]
        ) -> RunOutcome:
            return await stream_approved_plan_reply(
                run.event_sink,
                run.session_id,
                plan,
                run.run_id,
                run.cancellation,
                run.approval_broker,
                run.permission_preset,
                run.permission_profile,
            )

        try:
            run, plan, idempotent = await run_coordinator.start_plan_execution(
                session_id=session_id,
                plan_id=plan_id,
                request_id=request_id,
                retry=retry,
                execute=execute,
            )
        except run_repository.SessionBusyError as error:
            await self._send_session_busy(session_id, error.run_id)
            return
        except run_repository.PlanNotRetryableError as error:
            await self.sender.send_json(
                {
                    "type": "plan_error",
                    "code": "plan_not_retryable",
                    "session_id": session_id,
                    "plan_id": plan_id,
                    "message": str(error),
                }
            )
            return

        if idempotent:
            await self.sender.send_json(
                {
                    "type": "plan_execution_attached",
                    "session_id": session_id,
                    "plan_id": plan_id,
                    "run_id": run["id"],
                    "status": run["status"],
                    "request_id": request_id,
                }
            )
        else:
            await self.sender.send_json(
                {
                    "type": "plan_execution_created",
                    "session_id": session_id,
                    "plan_id": plan["id"],
                    "run_id": run["id"],
                    "status": "executing",
                    "request_id": request_id,
                }
            )

    async def _resolve_approval(self, payload: Mapping[str, Any]) -> None:
        run_id = str(payload.get("run_id", "")).strip()
        session_id = await self._session_id_for_run(payload, run_id)
        if session_id is None:
            await self.sender.send_json(
                {
                    "type": "approval_error",
                    "run_id": run_id,
                    "code": "run_not_found",
                }
            )
            return
        try:
            await run_coordinator.resolve_approval(
                session_id=session_id,
                run_id=run_id,
                approval_id=str(payload.get("approval_id", "")),
                decision=str(payload.get("decision", "")),
            )
        except run_repository.RunNotFoundError:
            await self.sender.send_json(
                {
                    "type": "approval_error",
                    "run_id": run_id,
                    "code": "run_not_found",
                }
            )
        except ApprovalResolutionError as error:
            await self.sender.send_json(
                {
                    "type": "approval_error",
                    "run_id": run_id,
                    "code": str(error),
                }
            )

    async def _handle_cancel(self, payload: Mapping[str, Any]) -> None:
        run_id = str(payload.get("run_id", "")).strip()
        session_id = await self._session_id_for_run(payload, run_id)
        if session_id is None:
            await self.sender.send_json(
                {
                    "type": "run_error",
                    "code": "run_not_found",
                    "run_id": run_id,
                }
            )
            return
        try:
            await run_coordinator.cancel(
                session_id=session_id,
                run_id=run_id,
            )
        except (run_repository.RunNotFoundError, run_repository.RunStateError):
            await self.sender.send_json(
                {
                    "type": "run_error",
                    "code": "run_not_found",
                    "run_id": run_id,
                }
            )

    async def _resume_run(self, payload: Mapping[str, Any]) -> None:
        run_id = str(payload.get("run_id", "")).strip()
        session_id = str(payload.get("session_id", "")).strip()
        try:
            after_sequence = int(payload.get("after_sequence", 0))
        except (TypeError, ValueError):
            after_sequence = -1
        try:
            run = await run_repository_call(
                run_repository.get_session_run,
                session_id,
                run_id,
            )
        except (ValueError, run_repository.RunNotFoundError):
            await self._send_cursor_error(session_id, run_id)
            return
        if after_sequence < 0 or after_sequence > int(run["last_sequence"]):
            await self._send_cursor_error(session_id, run_id)
            return

        watermark = int(run["last_sequence"])
        await self.sender.begin_replay(run_id)
        await self.sender.send_json(
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
                events = await run_repository_call(
                    run_repository.list_events,
                    run_id,
                    after_sequence=cursor,
                    through_sequence=watermark,
                    limit=1000,
                )
            except run_repository.EventCursorError:
                await self.sender.abort_replay(run_id)
                await self._send_cursor_error(session_id, run_id)
                return
            if not events:
                break
            for event in events:
                await self.sender.send_replay_event(event)
            cursor = int(events[-1]["seq"])
        latest = await run_repository_call(run_repository.get_run, run_id)
        await self.sender.finish_replay(
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

    async def _session_id_for_run(
        self, payload: Mapping[str, Any], run_id: str
    ) -> str | None:
        requested_session_id = str(payload.get("session_id", "")).strip()
        try:
            run = await run_repository_call(run_repository.get_run, run_id)
        except run_repository.RunNotFoundError:
            return None
        session_id = str(run["session_id"])
        if requested_session_id and requested_session_id != session_id:
            return None
        return session_id

    async def _send_session_busy(self, session_id: str, run_id: str) -> None:
        await self.sender.send_json(
            {
                "type": "run_error",
                "code": "session_busy",
                "session_id": session_id,
                "run_id": run_id,
                "message": "This session already has an active run.",
            }
        )

    async def _send_cursor_error(self, session_id: str, run_id: str) -> None:
        await self.sender.send_json(
            {
                "type": "run_error",
                "code": "event_cursor_invalid",
                "session_id": session_id,
                "run_id": run_id,
                "message": "The requested run event cursor is invalid.",
            }
        )
