from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from automata_api.agent.execution.windows_job import WindowsJob

logger = logging.getLogger(__name__)


@dataclass(eq=False)
class ManagedProcess:
    process: Any
    run_id: str | None
    session_id: str | None
    tool_call_id: str | None
    workspace: str | None
    windows_job: WindowsJob | None = None


@dataclass(frozen=True)
class ProcessExecutionScope:
    run_id: str
    tool_call_id: str
    session_id: str | None = None
    workspace: str | None = None


_process_scope: ContextVar[ProcessExecutionScope | None] = ContextVar(
    "automata_process_scope", default=None
)


def subprocess_group_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


@contextmanager
def process_execution_scope(
    run_id: str,
    tool_call_id: str,
    *,
    session_id: str | None = None,
    workspace: str | None = None,
) -> Iterator[None]:
    token = _process_scope.set(
        ProcessExecutionScope(
            run_id=run_id,
            tool_call_id=tool_call_id,
            session_id=session_id,
            workspace=workspace,
        )
    )
    try:
        yield
    finally:
        _process_scope.reset(token)


def current_process_scope() -> ProcessExecutionScope | None:
    return _process_scope.get()


class ProcessSupervisor:
    def __init__(self) -> None:
        self._managed: set[ManagedProcess] = set()
        self._lock = asyncio.Lock()

    async def register(self, process: Any) -> ManagedProcess:
        scope = _process_scope.get()
        windows_job = WindowsJob.assign(process.pid)
        if os.name == "nt" and windows_job is None:
            logger.warning(
                "Windows Job Object assignment failed for pid %s; "
                "using taskkill /T fallback for this process.",
                process.pid,
            )
        managed = ManagedProcess(
            process=process,
            run_id=scope.run_id if scope else None,
            session_id=scope.session_id if scope else None,
            tool_call_id=scope.tool_call_id if scope else None,
            workspace=scope.workspace if scope else None,
            windows_job=windows_job,
        )
        async with self._lock:
            self._managed.add(managed)
        return managed

    async def unregister(self, managed: ManagedProcess) -> None:
        async with self._lock:
            self._managed.discard(managed)
        if managed.windows_job is not None:
            managed.windows_job.close()

    async def active_count(self) -> int:
        async with self._lock:
            return len(self._managed)

    async def terminate(self, managed: ManagedProcess) -> None:
        process = managed.process
        if process.returncode is not None:
            return

        if managed.windows_job is not None:
            managed.windows_job.terminate()
        elif os.name == "nt":
            await _taskkill_tree(process.pid)
        else:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

        try:
            await asyncio.wait_for(asyncio.shield(process.wait()), timeout=5.0)
        except TimeoutError:
            if os.name != "nt":
                try:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()

    async def terminate_run(self, run_id: str) -> None:
        async with self._lock:
            targets = [item for item in self._managed if item.run_id == run_id]
        await asyncio.gather(*(self.terminate(item) for item in targets))

    async def terminate_all(self) -> None:
        async with self._lock:
            targets = list(self._managed)
        await asyncio.gather(*(self.terminate(item) for item in targets))


async def _taskkill_tree(pid: int) -> None:
    try:
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
    except OSError:
        return


process_supervisor = ProcessSupervisor()
