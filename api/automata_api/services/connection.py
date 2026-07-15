from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from automata_api.agent.execution.approval import (
    ApprovalBroker,
    ApprovalResolutionError,
)
from automata_api.agent.execution.model import CancellationToken
from automata_api.agent.execution.process import process_supervisor
from automata_api.agent.execution.runs import ActiveRun, active_run_registry
from automata_api.agent.status import agent_ready_message
from automata_api.config import get_api_config
from automata_api.repositories.sessions import (
    PlanNotFoundError,
    PlanStateError,
    SessionNotFoundError,
    approve_plan,
    save_context_message,
    save_message,
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
        self._cancelled_runs: set[str] = set()

    def mark_run_cancelled(self, run_id: str) -> None:
        self._cancelled_runs.add(run_id)

    def finish_cancelled_run(self, run_id: str) -> None:
        self._cancelled_runs.discard(run_id)

    def close(self) -> None:
        self._closed = True

    async def send_json(self, data: Any) -> None:
        if self._closed:
            return
        if isinstance(data, dict):
            run_id = data.get("run_id")
            if (
                isinstance(run_id, str)
                and run_id in self._cancelled_runs
                and data.get("type") not in {"run_cancel_requested", "run_cancelled"}
            ):
                return
        async with self._lock:
            if not self._closed:
                await self._websocket.send_json(data)


class AgentConnection:
    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.sender = SerializedWebSocketSender(websocket)
        self.connection_id = uuid.uuid4().hex
        self.active_run: ActiveRun | None = None

    async def serve(self) -> None:
        authenticated = await authenticate_websocket(
            self.websocket,
            allowed_origins=get_api_config().cors_origins,
        )
        if not authenticated:
            return

        await self.sender.send_json(
            {"type": "ready", "message": agent_ready_message()}
        )
        try:
            while True:
                payload = await receive_payload(self.websocket)
                await self._handle_payload(payload)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            self.sender.close()
            await self._cancel_active_run("WebSocket disconnected.", notify=False)

    async def _handle_payload(self, payload: dict[str, Any]) -> None:
        payload_type = payload.get("type")
        if payload_type == "tool_approval_response":
            await self._resolve_approval(payload)
            return
        if payload_type == "cancel_run":
            await self._handle_cancel(payload)
            return
        if payload_type not in {"prompt", "approve_plan"}:
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
            prompt = str(payload.get("prompt", "")).strip()
            if not prompt:
                await self.sender.send_json(
                    {"type": "error", "message": "Missing prompt"}
                )
                return
            await self._start_run(
                session_id,
                lambda run: self._execute_prompt(run, payload, prompt),
            )
            return

        plan_id = str(payload.get("plan_id", "")).strip()
        if not plan_id:
            await self.sender.send_json(
                {"type": "plan_error", "message": "Missing plan_id"}
            )
            return
        await self._start_run(
            session_id,
            lambda run: self._execute_plan_approval(run, plan_id),
        )

    async def _start_run(self, session_id: str, execute) -> None:
        if self.active_run is not None:
            await self.sender.send_json(
                {
                    "type": "run_error",
                    "code": "connection_busy",
                    "session_id": session_id,
                    "run_id": self.active_run.run_id,
                    "message": "This connection already has an active run.",
                }
            )
            return

        run_id = uuid.uuid4().hex
        cancellation = CancellationToken()
        broker = ApprovalBroker(
            run_id=run_id,
            session_id=session_id,
            emit=self.sender.send_json,
            cancellation=cancellation,
        )
        run = ActiveRun(
            run_id=run_id,
            session_id=session_id,
            owner_connection_id=self.connection_id,
            cancellation=cancellation,
            approval_broker=broker,
        )
        existing = await active_run_registry.claim(run)
        if existing is not None:
            await self.sender.send_json(
                {
                    "type": "run_error",
                    "code": "session_busy",
                    "session_id": session_id,
                    "run_id": existing.run_id,
                    "message": "This session already has an active run.",
                }
            )
            return

        self.active_run = run
        task = asyncio.create_task(self._run_wrapper(run, execute))
        await active_run_registry.attach_task(run_id, task)

    async def _run_wrapper(self, run: ActiveRun, execute) -> None:
        try:
            await execute(run)
        except asyncio.CancelledError:
            await process_supervisor.terminate_run(run.run_id)
            await self.sender.send_json(
                {
                    "type": "run_cancelled",
                    "run_id": run.run_id,
                    "session_id": run.session_id,
                    "message": run.cancellation.reason,
                }
            )
            self.sender.finish_cancelled_run(run.run_id)
        except Exception as error:
            await process_supervisor.terminate_run(run.run_id)
            await self.sender.send_json(
                {
                    "type": "error",
                    "run_id": run.run_id,
                    "message": f"Agent run failed: {error.__class__.__name__}",
                }
            )
        finally:
            run.approval_broker.cancel_all()
            await active_run_registry.release(run.run_id)
            if self.active_run is run:
                self.active_run = None

    async def _execute_prompt(
        self, run: ActiveRun, payload: dict[str, Any], prompt: str
    ) -> None:
        user_message = await run_repository_call(
            save_message,
            session_id=run.session_id,
            role="user",
            content=prompt,
        )
        await run_repository_call(
            save_context_message,
            session_id=run.session_id,
            message={"role": "user", "content": prompt},
        )
        if str(payload.get("mode", "")).strip() == "plan":
            await stream_plan_reply(
                self.sender,
                run.session_id,
                prompt,
                str(user_message["id"]),
                run.run_id,
                run.cancellation,
                run.approval_broker,
                payload.get("skills"),
            )
            return
        await stream_agent_reply(
            self.sender,
            run.session_id,
            prompt,
            run.run_id,
            run.cancellation,
            run.approval_broker,
            payload.get("skills"),
        )

    async def _execute_plan_approval(self, run: ActiveRun, plan_id: str) -> None:
        try:
            plan = await run_repository_call(approve_plan, run.session_id, plan_id)
        except SessionNotFoundError:
            await self.sender.send_json(
                {"type": "plan_error", "run_id": run.run_id, "message": "Session not found"}
            )
            return
        except PlanNotFoundError:
            await self.sender.send_json(
                {"type": "plan_error", "run_id": run.run_id, "message": "Plan not found"}
            )
            return
        except PlanStateError as error:
            await self.sender.send_json(
                {"type": "plan_error", "run_id": run.run_id, "message": str(error)}
            )
            return
        await stream_approved_plan_reply(
            self.sender,
            run.session_id,
            plan,
            run.run_id,
            run.cancellation,
            run.approval_broker,
        )

    async def _resolve_approval(self, payload: dict[str, Any]) -> None:
        run = self.active_run
        if run is None:
            await self.sender.send_json(
                {"type": "approval_error", "code": "run_not_found"}
            )
            return
        try:
            run.approval_broker.resolve(
                run_id=str(payload.get("run_id", "")),
                approval_id=str(payload.get("approval_id", "")),
                decision=str(payload.get("decision", "")),
            )
        except ApprovalResolutionError as error:
            await self.sender.send_json(
                {
                    "type": "approval_error",
                    "run_id": run.run_id,
                    "code": str(error),
                }
            )

    async def _handle_cancel(self, payload: dict[str, Any]) -> None:
        run = self.active_run
        requested_run_id = str(payload.get("run_id", "")).strip()
        if run is None or requested_run_id != run.run_id:
            await self.sender.send_json(
                {
                    "type": "run_error",
                    "code": "run_not_found",
                    "run_id": requested_run_id,
                }
            )
            return
        await self._cancel_active_run("Run cancelled by user.", notify=True)

    async def _cancel_active_run(self, reason: str, *, notify: bool) -> None:
        run = self.active_run
        if run is None:
            return
        self.sender.mark_run_cancelled(run.run_id)
        run.cancellation.cancel(reason)
        run.approval_broker.cancel_all()
        if notify:
            await self.sender.send_json(
                {
                    "type": "run_cancel_requested",
                    "run_id": run.run_id,
                    "session_id": run.session_id,
                }
            )
        if run.task is not None and not run.task.done():
            run.task.cancel()
            await asyncio.gather(run.task, return_exceptions=True)
