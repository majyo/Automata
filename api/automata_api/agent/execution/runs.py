import asyncio
from dataclasses import dataclass

from automata_api.agent.execution.approval import ApprovalBroker
from automata_api.agent.execution.model import CancellationToken


@dataclass
class ActiveRun:
    run_id: str
    session_id: str
    owner_connection_id: str
    cancellation: CancellationToken
    approval_broker: ApprovalBroker
    task: asyncio.Task[None] | None = None


class ActiveRunRegistry:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_session: dict[str, ActiveRun] = {}
        self._by_run: dict[str, ActiveRun] = {}

    async def claim(self, run: ActiveRun) -> ActiveRun | None:
        async with self._lock:
            existing = self._by_session.get(run.session_id)
            if existing is not None:
                return existing
            self._by_session[run.session_id] = run
            self._by_run[run.run_id] = run
            return None

    async def attach_task(self, run_id: str, task: asyncio.Task[None]) -> None:
        async with self._lock:
            run = self._by_run.get(run_id)
            if run is not None:
                run.task = task

    async def get(self, run_id: str) -> ActiveRun | None:
        async with self._lock:
            return self._by_run.get(run_id)

    async def release(self, run_id: str) -> None:
        async with self._lock:
            run = self._by_run.pop(run_id, None)
            if run is not None and self._by_session.get(run.session_id) is run:
                self._by_session.pop(run.session_id, None)


active_run_registry = ActiveRunRegistry()
