from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from automata_api.core.runs import state as run_state
from automata_api.core.runs.approval import ApprovalBroker, ApprovalResolutionError
from automata_api.core.runs.event_hub import RunEventHub
from automata_api.core.runs.event_hub import run_event_hub as default_run_event_hub
from automata_api.core.runs.events import DurableRunEventSink
from automata_api.core.runs.model import (
    CancellationToken,
    PromptSubmission,
    PublicRunError,
    RunInputGate,
    RunOutcome,
)
from automata_api.core.runs.ports import RunProcesses, RunStore
from automata_api.core.telemetry import observe_span
from automata_api.core.tools.permissions import (
    CompiledPermissionProfile,
    PermissionPreset,
    normalize_permission_preset,
    permission_profile_from_json,
)
from automata_api.core.tools.process_scope import process_resources

RunExecutor = Callable[["RunHandle"], Awaitable[RunOutcome]]
PromptRunExecutor = Callable[["RunHandle", dict[str, Any]], Awaitable[RunOutcome]]
PlanRunExecutor = Callable[["RunHandle", dict[str, Any]], Awaitable[RunOutcome]]


@dataclass
class RunHandle:
    run_id: str
    session_id: str
    permission_preset: PermissionPreset
    permission_profile: CompiledPermissionProfile
    cancellation: CancellationToken
    approval_broker: ApprovalBroker
    event_sink: DurableRunEventSink
    input_gate: RunInputGate
    prompt_executor: PromptRunExecutor | None = None
    created_at: str | None = None
    task: asyncio.Task[None] | None = None
    explicit_cancel: bool = False


class RunCoordinator:
    """Owns run lifetimes for one application instance.

    Bootstrap supplies storage, event publication and process resources.
    Tests may supply in-memory collaborators.
    """

    def __init__(
        self,
        *,
        event_hub: RunEventHub | None = None,
        process_supervisor: RunProcesses,
        process_sessions: RunProcesses,
        store: RunStore,
        run_event_retention_days: int | None = None,
    ) -> None:
        self.instance_id = uuid.uuid4().hex
        self._lock = asyncio.Lock()
        self._by_run: dict[str, RunHandle] = {}
        self._stopping = False
        self._hub = event_hub or default_run_event_hub
        self._processes = process_supervisor
        self._process_sessions = process_sessions
        self._store = store
        self._queue_lock = asyncio.Lock()
        retention = (
            run_event_retention_days
            if run_event_retention_days is not None
            else read_run_event_retention_days()
        )
        self._retention_days = max(0, retention)

    async def startup(self) -> list[dict[str, Any]]:
        self.instance_id = uuid.uuid4().hex
        self._stopping = False
        async with self._lock:
            self._by_run.clear()
        interrupted = await asyncio.to_thread(
            self._store.interrupt_stale_runs, self.instance_id
        )
        await asyncio.to_thread(
            self._store.prune_terminal_run_events,
            self._retention_days,
        )
        return interrupted

    async def shutdown(self) -> None:
        self._stopping = True
        async with self._lock:
            handles = tuple(self._by_run.values())
        for handle in handles:
            handle.cancellation.cancel("API process is shutting down.")
            handle.approval_broker.cancel_all()
            if handle.task is not None and not handle.task.done():
                handle.task.cancel()
        if handles:
            await asyncio.gather(
                *(handle.task for handle in handles if handle.task is not None),
                return_exceptions=True,
            )
        async with self._lock:
            self._by_run.clear()

    async def start_prompt(
        self,
        *,
        session_id: str,
        prompt: str,
        mode: str,
        execute: PromptRunExecutor,
        prompt_executor: PromptRunExecutor | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        resolved_mode = "plan" if mode == "plan" else "act"
        run, user_message = await asyncio.to_thread(
            self._store.create_prompt_run,
            session_id=session_id,
            prompt=prompt,
            mode=resolved_mode,
            owner_instance_id=self.instance_id,
        )
        await self._start_handle(
            run,
            lambda handle: execute(handle, user_message),
            prompt_executor=prompt_executor,
        )
        return run, user_message

    async def steer_prompt(
        self,
        *,
        session_id: str,
        run_id: str,
        prompt: str,
        request_id: str,
    ) -> PromptSubmission:
        existing = await asyncio.to_thread(
            self._store.get_input,
            session_id=session_id,
            request_id=request_id,
        )
        if existing is not None:
            ensure_input_request(
                existing,
                delivery="steer",
                prompt=prompt,
                target_run_id=run_id,
            )
            return PromptSubmission(
                delivery="steer",
                input=existing,
                run=await self._run_for_input(existing, run_id),
                idempotent=True,
            )

        handle = await self.get_handle(run_id)
        if handle is None or handle.session_id != session_id:
            raise run_state.RunNotSteerableError("Run is not accepting steering input.")

        async def enqueue() -> tuple[dict[str, Any], bool]:
            return await asyncio.to_thread(
                self._store.enqueue_steering_input,
                session_id=session_id,
                target_run_id=run_id,
                prompt=prompt,
                request_id=request_id,
            )

        result = await handle.input_gate.accept(enqueue)
        if result is None:
            existing = await asyncio.to_thread(
                self._store.get_input,
                session_id=session_id,
                request_id=request_id,
            )
            if existing is not None:
                ensure_input_request(
                    existing,
                    delivery="steer",
                    prompt=prompt,
                    target_run_id=run_id,
                )
                return PromptSubmission(
                    delivery="steer",
                    input=existing,
                    run=await self._run_for_input(existing, run_id),
                    idempotent=True,
                )
            raise run_state.RunNotSteerableError(
                "Run is no longer accepting steering input."
            )

        input_row, idempotent = result
        return PromptSubmission(
            delivery="steer",
            input=input_row,
            run=await self._run_for_input(input_row, run_id),
            idempotent=idempotent,
        )

    async def enqueue_prompt(
        self,
        *,
        session_id: str,
        prompt: str,
        mode: str,
        skills: Any,
        request_id: str,
        prompt_executor: PromptRunExecutor,
    ) -> PromptSubmission:
        resolved_mode = "plan" if mode == "plan" else "act"
        async with self._queue_lock:
            input_row, idempotent = await asyncio.to_thread(
                self._store.enqueue_queued_input,
                session_id=session_id,
                prompt=prompt,
                mode=resolved_mode,
                skills=skills,
                request_id=request_id,
            )
            run = await self._start_next_queued_locked(
                session_id=session_id,
                prompt_executor=prompt_executor,
            )
            if run is None and input_row.get("run_id"):
                run = await asyncio.to_thread(
                    self._store.get_run, str(input_row["run_id"])
                )
            return PromptSubmission(
                delivery="queue",
                input=input_row,
                run=run,
                idempotent=idempotent,
            )

    async def resume_queued_inputs(
        self, prompt_executor: PromptRunExecutor
    ) -> int:
        started = 0
        for session_id in await asyncio.to_thread(
            self._store.pending_queue_session_ids
        ):
            while True:
                async with self._queue_lock:
                    run = await self._start_next_queued_locked(
                        session_id=session_id,
                        prompt_executor=prompt_executor,
                    )
                if run is None:
                    break
                started += 1
        return started

    async def start_plan_execution(
        self,
        *,
        session_id: str,
        plan_id: str,
        request_id: str,
        retry: bool,
        execute: PlanRunExecutor,
        prompt_executor: PromptRunExecutor | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        run, plan, idempotent = await asyncio.to_thread(
            self._store.begin_plan_execution,
            session_id=session_id,
            plan_id=plan_id,
            request_id=request_id,
            owner_instance_id=self.instance_id,
            retry=retry,
        )
        if not idempotent and run["status"] == "queued":
            await self._start_handle(
                run,
                lambda handle: execute(handle, plan),
                prompt_executor=prompt_executor,
            )
        return run, plan, idempotent

    async def cancel(
        self,
        *,
        session_id: str,
        run_id: str,
        reason: str = "Run cancelled by user.",
    ) -> None:
        handle = await self.get_handle(run_id)
        if handle is None or handle.session_id != session_id:
            raise run_state.RunNotFoundError("Run not found")
        handle.explicit_cancel = True
        try:
            await asyncio.to_thread(
                self._store.transition_run,
                run_id,
                expected=("queued", "running", "waiting_approval"),
                target="cancelling",
            )
        except run_state.RunStateError:
            run = await asyncio.to_thread(self._store.get_run, run_id)
            if run["status"] in run_state.TERMINAL_STATUSES:
                raise
        await handle.event_sink.send_json(
            {
                "type": "run_cancel_requested",
                "message": reason,
            }
        )
        handle.cancellation.cancel(reason)
        handle.approval_broker.cancel_all()
        if handle.task is not None and not handle.task.done():
            handle.task.cancel()
            await asyncio.gather(handle.task, return_exceptions=True)

    async def resolve_approval(
        self,
        *,
        session_id: str,
        run_id: str,
        approval_id: str,
        decision: str,
    ) -> None:
        handle = await self.get_handle(run_id)
        if handle is None or handle.session_id != session_id:
            raise run_state.RunNotFoundError("Run not found")
        handle.approval_broker.resolve(
            run_id=run_id,
            approval_id=approval_id,
            decision=decision,
        )

    async def get_handle(self, run_id: str) -> RunHandle | None:
        async with self._lock:
            return self._by_run.get(run_id)

    async def _start_handle(
        self,
        run: dict[str, Any],
        execute: RunExecutor,
        *,
        prompt_executor: PromptRunExecutor | None = None,
    ) -> RunHandle:
        cancellation = CancellationToken()
        event_sink = DurableRunEventSink(
            run_id=str(run["id"]),
            store=self._store,
            hub=self._hub,
        )
        broker = ApprovalBroker(
            run_id=str(run["id"]),
            session_id=str(run["session_id"]),
            emit=event_sink.send_json,
            cancellation=cancellation,
        )
        handle = RunHandle(
            run_id=str(run["id"]),
            session_id=str(run["session_id"]),
            permission_preset=normalize_permission_preset(run["permission_preset"]),
            permission_profile=permission_profile_from_json(
                str(run["permission_profile_json"])
            ),
            cancellation=cancellation,
            approval_broker=broker,
            event_sink=event_sink,
            input_gate=RunInputGate(),
            prompt_executor=prompt_executor,
            created_at=(str(run["created_at"]) if run.get("created_at") else None),
        )
        async with self._lock:
            self._by_run[handle.run_id] = handle
        handle.task = asyncio.create_task(self._run_wrapper(handle, execute))
        return handle

    async def _run_wrapper(self, handle: RunHandle, execute: RunExecutor) -> None:
        with process_resources(self._processes, self._process_sessions):
            async with observe_span(
                "agent.run",
                attributes={
                    "owner_instance_id": self.instance_id,
                    "queue_delay_ns": queue_delay_ns(handle.created_at),
                },
                run_id=handle.run_id,
                session_id=handle.session_id,
                root=True,
                critical=True,
            ) as run_span:
                await self._run_wrapper_observed(handle, execute, run_span)

    async def _run_wrapper_observed(
        self,
        handle: RunHandle,
        execute: RunExecutor,
        run_span: Any,
    ) -> None:
        try:
            await asyncio.to_thread(
                self._store.transition_run,
                handle.run_id,
                expected=("queued",),
                target="running",
            )
            outcome = await execute(handle)
            await self._process_sessions.terminate_run(handle.run_id)
            await self._processes.terminate_run(handle.run_id)
            await handle.event_sink.flush()
            previous = await asyncio.to_thread(self._store.get_run, handle.run_id)
            terminal = await asyncio.to_thread(
                self._store.finish_run,
                handle.run_id,
                status="completed",
                event={"type": "done"},
                response_content=outcome.response_content,
                plan_content=outcome.plan_content,
            )
            committed_events = await asyncio.to_thread(
                self._store.list_events,
                handle.run_id,
                after_sequence=int(previous["last_sequence"]),
            )
            for event in committed_events:
                await handle.event_sink.broadcast_persisted(event)
            run_span.set_attributes(run_status="completed")
            await self._start_next_queued(
                session_id=handle.session_id,
                prompt_executor=handle.prompt_executor,
            )
        except asyncio.CancelledError:
            await self._process_sessions.terminate_run(handle.run_id)
            await self._processes.terminate_run(handle.run_id)
            await handle.event_sink.flush()
            status = (
                "interrupted"
                if self._stopping and not handle.explicit_cancel
                else "cancelled"
            )
            code = (
                "api_process_shutdown"
                if status == "interrupted"
                else "cancelled_by_user"
            )
            event_type = (
                "run_interrupted" if status == "interrupted" else "run_cancelled"
            )
            terminal = await asyncio.to_thread(
                self._store.finish_run,
                handle.run_id,
                status=status,
                event={
                    "type": event_type,
                    "code": code,
                    "message": handle.cancellation.reason,
                },
                error_code=code,
                public_error=handle.cancellation.reason,
            )
            await handle.event_sink.broadcast_persisted(terminal)
            run_span.set_status(status, error_type=code)
            run_span.set_attributes(run_status=status)
        except PublicRunError as error:
            await self._process_sessions.terminate_run(handle.run_id)
            await self._processes.terminate_run(handle.run_id)
            await handle.event_sink.flush()
            terminal = await asyncio.to_thread(
                self._store.finish_run,
                handle.run_id,
                status="failed",
                event={
                    "type": "error",
                    "code": error.code,
                    "message": error.public_message,
                },
                error_code=error.code,
                public_error=error.public_message,
            )
            await handle.event_sink.broadcast_persisted(terminal)
            run_span.set_status("error", error_type=error.code)
            run_span.set_attributes(run_status="failed")
        except Exception as error:
            await self._process_sessions.terminate_run(handle.run_id)
            await self._processes.terminate_run(handle.run_id)
            await handle.event_sink.flush()
            public_message = f"Agent run failed: {error.__class__.__name__}"
            terminal = await asyncio.to_thread(
                self._store.finish_run,
                handle.run_id,
                status="failed",
                event={
                    "type": "error",
                    "code": "run_failed",
                    "message": public_message,
                },
                error_code="run_failed",
                public_error=public_message,
            )
            await handle.event_sink.broadcast_persisted(terminal)
            run_span.set_status("error", error_type=error.__class__.__name__)
            run_span.set_attributes(run_status="failed")
        finally:
            cancel_inputs = getattr(
                self._store, "cancel_unapplied_inputs_for_run", None
            )
            if cancel_inputs is not None:
                await asyncio.to_thread(
                    cancel_inputs,
                    handle.run_id,
                    error_code="run_terminated",
                )
            handle.approval_broker.cancel_all()
            await handle.event_sink.close()
            async with self._lock:
                self._by_run.pop(handle.run_id, None)

    async def _run_for_input(
        self, input_row: dict[str, Any], fallback_run_id: str
    ) -> dict[str, Any] | None:
        run_id = input_row.get("run_id") or input_row.get("target_run_id")
        if not run_id:
            run_id = fallback_run_id
        try:
            return await asyncio.to_thread(self._store.get_run, str(run_id))
        except run_state.RunNotFoundError:
            return None

    async def _start_next_queued(
        self,
        *,
        session_id: str,
        prompt_executor: PromptRunExecutor | None,
    ) -> dict[str, Any] | None:
        if prompt_executor is None or self._stopping:
            return None
        async with self._queue_lock:
            return await self._start_next_queued_locked(
                session_id=session_id,
                prompt_executor=prompt_executor,
            )

    async def _start_next_queued_locked(
        self,
        *,
        session_id: str,
        prompt_executor: PromptRunExecutor,
    ) -> dict[str, Any] | None:
        claimed = await asyncio.to_thread(
            self._store.claim_next_queued_input,
            session_id=session_id,
            owner_instance_id=self.instance_id,
        )
        if claimed is None:
            return None
        run, input_row, _message = claimed
        await self._start_handle(
            run,
            lambda handle: prompt_executor(handle, input_row),
            prompt_executor=prompt_executor,
        )
        return run


def read_run_event_retention_days() -> int:
    """Retention window for pruned run events, in days.

    Kept as a module level helper so ``RunCoordinator`` can still be
    constructed without the container; ``bootstrap.settings`` owns the
    authoritative snapshot used by real application instances.
    """
    raw = os.environ.get("AUTOMATA_RUN_EVENT_RETENTION_DAYS", "").strip()
    if not raw:
        return 30
    try:
        return max(0, min(int(raw), 3650))
    except ValueError:
        return 30


def ensure_input_request(
    input_row: dict[str, Any], *, delivery: str, prompt: str, target_run_id: str = ""
) -> None:
    if (
        input_row.get("delivery") != delivery
        or input_row.get("prompt") != prompt
        or (target_run_id and input_row.get("target_run_id") != target_run_id)
    ):
        raise run_state.InputConflictError(
            "request_id was already used for a different input."
        )


def queue_delay_ns(created_at: str | None) -> int | None:
    if not created_at:
        return None
    try:
        created = datetime.fromisoformat(created_at)
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return max(
            0,
            int(
                (datetime.now(UTC) - created.astimezone(UTC)).total_seconds()
                * 1_000_000_000
            ),
        )
    except ValueError:
        return None


__all__ = [
    "ApprovalResolutionError",
    "RunCoordinator",
    "RunHandle",
]
