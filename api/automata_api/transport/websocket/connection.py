from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any, cast

from fastapi import WebSocket, WebSocketDisconnect

from automata_api.bootstrap.status import agent_ready_message
from automata_api.config import get_api_config
from automata_api.core.runs import state as run_repository
from automata_api.core.runs.approval import ApprovalResolutionError
from automata_api.core.runs.coordinator import RunCoordinator
from automata_api.core.runs.event_hub import RunEventHub
from automata_api.core.runs.replay import ReplayService
from automata_api.core.runs.service import RunService
from automata_api.core.runs.turns import run_repository_call
from automata_api.core.sessions.ports import SessionStore
from automata_api.transport.schemas import ChatPayload
from automata_api.transport.security import authenticate_websocket
from automata_api.transport.websocket.commands import (
    ApprovalResponseCommand,
    CancelRunCommand,
    InvalidCommand,
    PlanExecutionCommand,
    PromptCommand,
    ResumeRunCommand,
    decode_command,
)
from automata_api.transport.websocket.sender import SerializedWebSocketSender


class AgentConnection:
    """One WebSocket connection.

    The connection owns only connection scoped state (the serialized
    sender, the replay buffers and the subscription). Run coordination and
    event fan-out come from the application container, and runs outlive the
    connection: closing a socket unsubscribes and releases the sender but
    never cancels a Run.
    """

    def __init__(
        self,
        websocket: WebSocket,
        *,
        coordinator: RunCoordinator,
        event_hub: RunEventHub,
        session_store: SessionStore,
        replay: ReplayService,
        runs: RunService,
    ) -> None:
        self.websocket = websocket
        self.coordinator = coordinator
        self.event_hub = event_hub
        self.session_store = session_store
        self.replay = replay
        self.runs = runs
        self.sender = SerializedWebSocketSender(websocket)
        self.connection_id = uuid.uuid4().hex

    async def serve(self) -> None:
        authenticated = await authenticate_websocket(
            self.websocket,
            allowed_origins=get_api_config().cors_origins,
        )
        if not authenticated:
            return

        await self.event_hub.register(self.sender)
        try:
            active_runs = await self.runs.active_runs()
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
            await self.event_hub.unregister(self.sender)
            self.sender.close()

    async def _handle_payload(self, payload: Mapping[str, Any]) -> None:
        command = decode_command(payload)
        if isinstance(command, InvalidCommand):
            await self._send_command_error(command)
            return
        if isinstance(command, ApprovalResponseCommand):
            await self._resolve_approval(command)
            return
        if isinstance(command, CancelRunCommand):
            await self._handle_cancel(command)
            return
        if isinstance(command, ResumeRunCommand):
            await self._resume_run(command)
            return

        if not await run_repository_call(
            self.session_store.session_exists, command.session_id
        ):
            await self.sender.send_json(
                {"type": "error", "message": "Session not found"}
            )
            return

        if isinstance(command, PromptCommand):
            await self._start_prompt(command)
            return
        await self._start_plan_execution(command)

    async def _send_command_error(self, command: InvalidCommand) -> None:
        """Report a malformed frame using the envelope the client expects.

        A rejected plan retry carries the plan id so the UI can re-prompt,
        which is why it uses ``plan_error`` rather than the generic ``error``
        envelope.
        """
        if command.code is None:
            await self.sender.send_json({"type": "error", "message": command.message})
            return
        await self.sender.send_json(
            {
                "type": "plan_error",
                "code": command.code,
                "message": command.message,
            }
        )

    async def _start_prompt(self, command: PromptCommand) -> None:
        session_id = command.session_id
        prompt = command.prompt
        mode = command.mode

        try:
            await self.runs.start_prompt(
                session_id=session_id,
                prompt=prompt,
                mode=mode,
                skills=command.skills,
            )
        except run_repository.SessionBusyError as error:
            await self._send_session_busy(session_id, error.run_id)

    async def _start_plan_execution(self, command: PlanExecutionCommand) -> None:
        session_id = command.session_id
        plan_id = command.plan_id
        request_id = command.request_id
        retry = command.retry

        try:
            run, plan, idempotent = await self.runs.start_plan_execution(
                session_id=session_id,
                plan_id=plan_id,
                request_id=request_id,
                retry=retry,
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

    async def _resolve_approval(self, command: ApprovalResponseCommand) -> None:
        run_id = command.run_id
        session_id = await self.runs.session_id_for_run(command.session_id, run_id)
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
            await self.coordinator.resolve_approval(
                session_id=session_id,
                run_id=run_id,
                approval_id=command.approval_id,
                decision=command.decision,
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

    async def _handle_cancel(self, command: CancelRunCommand) -> None:
        run_id = command.run_id
        session_id = await self.runs.session_id_for_run(command.session_id, run_id)
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
            await self.coordinator.cancel(
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

    async def _resume_run(self, command: ResumeRunCommand) -> None:
        outcome = await self.replay.resume(
            sender=self.sender,
            session_id=command.session_id,
            run_id=command.run_id,
            after_sequence=command.after_sequence,
        )
        if not outcome.ok and outcome.cursor_error:
            await self._send_cursor_error(command.session_id, command.run_id)

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


async def receive_payload(websocket) -> ChatPayload:
    message = await websocket.receive_text()
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        return {"type": "invalid"}

    if not isinstance(payload, dict):
        return {"type": "invalid"}

    return cast(ChatPayload, payload)
