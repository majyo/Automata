from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from automata_api.agent.execution.approval import (
    ApprovalBroker,
    ApprovalResolutionError,
)
from automata_api.agent.execution.events import DurableRunEventSink
from automata_api.agent.execution.model import (
    CancellationToken,
    PublicRunError,
    RunOutcome,
)
from automata_api.agent.execution.process import process_supervisor
from automata_api.repositories import runs as run_repository


RunExecutor = Callable[["RunHandle"], Awaitable[RunOutcome]]
PromptRunExecutor = Callable[
    ["RunHandle", dict[str, Any]], Awaitable[RunOutcome]
]
PlanRunExecutor = Callable[
    ["RunHandle", dict[str, Any]], Awaitable[RunOutcome]
]


@dataclass
class RunHandle:
    run_id: str
    session_id: str
    cancellation: CancellationToken
    approval_broker: ApprovalBroker
    event_sink: DurableRunEventSink
    task: asyncio.Task[None] | None = None
    explicit_cancel: bool = False


class RunCoordinator:
    def __init__(self) -> None:
        self.instance_id = uuid.uuid4().hex
        self._lock = asyncio.Lock()
        self._by_run: dict[str, RunHandle] = {}
        self._stopping = False

    async def startup(self) -> list[dict[str, Any]]:
        self.instance_id = uuid.uuid4().hex
        self._stopping = False
        async with self._lock:
            self._by_run.clear()
        interrupted = await asyncio.to_thread(
            run_repository.interrupt_stale_runs, self.instance_id
        )
        await asyncio.to_thread(
            run_repository.prune_terminal_run_events,
            run_event_retention_days(),
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
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        resolved_mode = "plan" if mode == "plan" else "act"
        run, user_message = await asyncio.to_thread(
            run_repository.create_prompt_run,
            session_id=session_id,
            prompt=prompt,
            mode=resolved_mode,
            owner_instance_id=self.instance_id,
        )
        await self._start_handle(
            run, lambda handle: execute(handle, user_message)
        )
        return run, user_message

    async def start_plan_execution(
        self,
        *,
        session_id: str,
        plan_id: str,
        request_id: str,
        retry: bool,
        execute: PlanRunExecutor,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        run, plan, idempotent = await asyncio.to_thread(
            run_repository.begin_plan_execution,
            session_id=session_id,
            plan_id=plan_id,
            request_id=request_id,
            owner_instance_id=self.instance_id,
            retry=retry,
        )
        if not idempotent and run["status"] == "queued":
            await self._start_handle(run, lambda handle: execute(handle, plan))
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
            raise run_repository.RunNotFoundError("Run not found")
        handle.explicit_cancel = True
        try:
            await asyncio.to_thread(
                run_repository.transition_run,
                run_id,
                expected=("queued", "running", "waiting_approval"),
                target="cancelling",
            )
        except run_repository.RunStateError:
            run = await asyncio.to_thread(run_repository.get_run, run_id)
            if run["status"] in run_repository.TERMINAL_STATUSES:
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
            raise run_repository.RunNotFoundError("Run not found")
        handle.approval_broker.resolve(
            run_id=run_id,
            approval_id=approval_id,
            decision=decision,
        )

    async def get_handle(self, run_id: str) -> RunHandle | None:
        async with self._lock:
            return self._by_run.get(run_id)

    async def _start_handle(
        self, run: dict[str, Any], execute: RunExecutor
    ) -> RunHandle:
        cancellation = CancellationToken()
        event_sink = DurableRunEventSink(run_id=str(run["id"]))
        broker = ApprovalBroker(
            run_id=str(run["id"]),
            session_id=str(run["session_id"]),
            emit=event_sink.send_json,
            cancellation=cancellation,
        )
        handle = RunHandle(
            run_id=str(run["id"]),
            session_id=str(run["session_id"]),
            cancellation=cancellation,
            approval_broker=broker,
            event_sink=event_sink,
        )
        async with self._lock:
            self._by_run[handle.run_id] = handle
        handle.task = asyncio.create_task(self._run_wrapper(handle, execute))
        return handle

    async def _run_wrapper(
        self, handle: RunHandle, execute: RunExecutor
    ) -> None:
        try:
            await asyncio.to_thread(
                run_repository.transition_run,
                handle.run_id,
                expected=("queued",),
                target="running",
            )
            outcome = await execute(handle)
            await handle.event_sink.flush()
            previous = await asyncio.to_thread(
                run_repository.get_run, handle.run_id
            )
            terminal = await asyncio.to_thread(
                run_repository.finish_run,
                handle.run_id,
                status="completed",
                event={"type": "done"},
                response_content=outcome.response_content,
                plan_content=outcome.plan_content,
            )
            committed_events = await asyncio.to_thread(
                run_repository.list_events,
                handle.run_id,
                after_sequence=int(previous["last_sequence"]),
            )
            for event in committed_events:
                await handle.event_sink.broadcast_persisted(event)
        except asyncio.CancelledError:
            await process_supervisor.terminate_run(handle.run_id)
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
                run_repository.finish_run,
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
        except PublicRunError as error:
            await process_supervisor.terminate_run(handle.run_id)
            await handle.event_sink.flush()
            terminal = await asyncio.to_thread(
                run_repository.finish_run,
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
        except Exception as error:
            await process_supervisor.terminate_run(handle.run_id)
            await handle.event_sink.flush()
            public_message = f"Agent run failed: {error.__class__.__name__}"
            terminal = await asyncio.to_thread(
                run_repository.finish_run,
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
        finally:
            handle.approval_broker.cancel_all()
            await handle.event_sink.close()
            async with self._lock:
                self._by_run.pop(handle.run_id, None)


run_coordinator = RunCoordinator()


def run_event_retention_days() -> int:
    raw = os.environ.get("AUTOMATA_RUN_EVENT_RETENTION_DAYS", "").strip()
    if not raw:
        return 30
    try:
        return max(0, min(int(raw), 3650))
    except ValueError:
        return 30


__all__ = [
    "ApprovalResolutionError",
    "RunCoordinator",
    "RunHandle",
    "run_coordinator",
]
