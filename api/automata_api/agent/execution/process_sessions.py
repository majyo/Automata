from __future__ import annotations

import asyncio
import codecs
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from automata_api.agent.execution.process import (
    ManagedProcess,
    current_process_scope,
    process_supervisor,
)


MAX_LIVE_PROCESS_SESSIONS = 8
PROCESS_SESSION_IDLE_SECONDS = 60.0
PROCESS_SESSION_TRANSCRIPT_CHARS = 60_000
PROCESS_SESSION_READ_CHUNK_BYTES = 8192


class ProcessSessionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _HeadTailBuffer:
    _MARKER = "\n... output truncated ...\n"

    def __init__(self, max_chars: int) -> None:
        self.max_chars = max(0, max_chars)
        self._value = ""
        self._head = ""
        self._tail = ""
        self.truncated = False

    def append(self, text: str) -> None:
        if not text:
            return
        if self.max_chars <= 0:
            self.truncated = True
            return
        if not self.truncated:
            combined = self._value + text
            if len(combined) <= self.max_chars:
                self._value = combined
                return
            self.truncated = True
            marker = self._marker()
            available = max(0, self.max_chars - len(marker))
            head_chars = available // 2
            tail_chars = available - head_chars
            self._head = combined[:head_chars]
            self._tail = combined[-tail_chars:] if tail_chars else ""
            self._value = ""
            return
        tail_chars = self._tail_limit()
        if tail_chars:
            self._tail = (self._tail + text)[-tail_chars:]

    @property
    def text(self) -> str:
        if not self.truncated:
            return self._value
        return f"{self._head}{self._marker()}{self._tail}"

    @property
    def has_content(self) -> bool:
        return bool(self._value or self._head or self._tail)

    def _marker(self) -> str:
        if self.max_chars < len(self._MARKER) + 2:
            return ""
        return self._MARKER

    def _tail_limit(self) -> int:
        available = max(0, self.max_chars - len(self._marker()))
        return available - available // 2


@dataclass(frozen=True)
class ProcessSessionSnapshot:
    session_id: str
    running: bool
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool


@dataclass(eq=False)
class ProcessSessionEntry:
    session_id: str
    run_id: str
    conversation_session_id: str
    tool_call_id: str
    workspace: str
    process: Any
    managed: ManagedProcess
    created_at: float
    last_used_at: float
    timeout_seconds: float
    pending_limit: int
    timed_out: bool = False
    stdout_pending: _HeadTailBuffer = field(init=False)
    stderr_pending: _HeadTailBuffer = field(init=False)
    stdout_transcript: _HeadTailBuffer = field(init=False)
    stderr_transcript: _HeadTailBuffer = field(init=False)
    activity: asyncio.Event = field(default_factory=asyncio.Event)
    stdout_task: asyncio.Task[None] | None = None
    stderr_task: asyncio.Task[None] | None = None
    wait_task: asyncio.Task[int] | None = None
    timeout_task: asyncio.Task[None] | None = None
    idle_task: asyncio.Task[None] | None = None

    def __post_init__(self) -> None:
        self.stdout_pending = _HeadTailBuffer(self.pending_limit)
        self.stderr_pending = _HeadTailBuffer(self.pending_limit)
        self.stdout_transcript = _HeadTailBuffer(PROCESS_SESSION_TRANSCRIPT_CHARS)
        self.stderr_transcript = _HeadTailBuffer(PROCESS_SESSION_TRANSCRIPT_CHARS)

    @property
    def has_pending_output(self) -> bool:
        return self.stdout_pending.has_content or self.stderr_pending.has_content

    def take_pending(self) -> tuple[str, str, bool, bool]:
        stdout = self.stdout_pending
        stderr = self.stderr_pending
        self.stdout_pending = _HeadTailBuffer(self.pending_limit)
        self.stderr_pending = _HeadTailBuffer(self.pending_limit)
        return stdout.text, stderr.text, stdout.truncated, stderr.truncated


class ProcessSessionManager:
    def __init__(self) -> None:
        self._entries: dict[str, ProcessSessionEntry] = {}
        self._lock = asyncio.Lock()

    async def active_count(self) -> int:
        async with self._lock:
            return len(self._entries)

    async def start(
        self,
        process: Any,
        *,
        timeout_seconds: float,
        pending_limit: int,
    ) -> str:
        scope = current_process_scope()
        if (
            scope is None
            or scope.session_id is None
            or scope.workspace is None
        ):
            managed = await process_supervisor.register(process)
            await process_supervisor.terminate(managed)
            await process_supervisor.unregister(managed)
            raise ProcessSessionError(
                "live_process_context_required",
                "Live process sessions require an active Run, session, and workspace.",
            )

        async with self._lock:
            if len(self._entries) >= MAX_LIVE_PROCESS_SESSIONS:
                managed = await process_supervisor.register(process)
                await process_supervisor.terminate(managed)
                await process_supervisor.unregister(managed)
                raise ProcessSessionError(
                    "live_process_limit_reached",
                    f"At most {MAX_LIVE_PROCESS_SESSIONS} live process sessions are allowed.",
                )

            managed = await process_supervisor.register(process)
            now = time.monotonic()
            session_id = f"proc_{uuid.uuid4().hex}"
            entry = ProcessSessionEntry(
                session_id=session_id,
                run_id=scope.run_id,
                conversation_session_id=scope.session_id,
                tool_call_id=scope.tool_call_id,
                workspace=scope.workspace,
                process=process,
                managed=managed,
                created_at=now,
                last_used_at=now,
                timeout_seconds=timeout_seconds,
                pending_limit=min(
                    max(1, pending_limit),
                    PROCESS_SESSION_TRANSCRIPT_CHARS,
                ),
            )
            self._entries[session_id] = entry

        entry.stdout_task = asyncio.create_task(
            self._read_stream(entry, process.stdout, "stdout")
        )
        entry.stderr_task = asyncio.create_task(
            self._read_stream(entry, process.stderr, "stderr")
        )
        entry.wait_task = asyncio.create_task(self._wait_for_process(entry))
        entry.timeout_task = asyncio.create_task(self._timeout_session(entry))
        entry.idle_task = asyncio.create_task(self._evict_idle_session(entry))
        return session_id

    async def interact(
        self,
        session_id: str,
        *,
        chars: str,
        yield_time_ms: int,
    ) -> ProcessSessionSnapshot:
        entry = await self._authorized_entry(session_id)
        entry.last_used_at = time.monotonic()

        if chars:
            stdin = entry.process.stdin
            if stdin is None or stdin.is_closing():
                raise ProcessSessionError(
                    "process_stdin_closed",
                    "The process stdin pipe is closed.",
                )
            try:
                stdin.write(chars.encode("utf-8"))
                await stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as error:
                raise ProcessSessionError(
                    "process_stdin_closed",
                    "The process stdin pipe is closed.",
                ) from error

        if yield_time_ms > 0:
            deadline = time.monotonic() + yield_time_ms / 1000
            while (
                entry.process.returncode is None
                and not entry.timed_out
                and not entry.has_pending_output
            ):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                entry.activity.clear()
                if (
                    entry.process.returncode is not None
                    or entry.timed_out
                    or entry.has_pending_output
                ):
                    break
                try:
                    await asyncio.wait_for(entry.activity.wait(), timeout=remaining)
                except TimeoutError:
                    break

        if entry.process.returncode is not None or entry.timed_out:
            await self._finish_readers(entry)

        stdout, stderr, stdout_truncated, stderr_truncated = entry.take_pending()
        running = entry.process.returncode is None and not entry.timed_out
        snapshot = ProcessSessionSnapshot(
            session_id=entry.session_id,
            running=running,
            exit_code=entry.process.returncode if not entry.timed_out else None,
            timed_out=entry.timed_out,
            stdout=stdout,
            stderr=stderr,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )
        if not running:
            await self._remove_entry(entry)
        return snapshot

    async def terminate_run(self, run_id: str) -> None:
        async with self._lock:
            targets = [entry for entry in self._entries.values() if entry.run_id == run_id]
        await asyncio.gather(
            *(self._terminate_and_remove(entry) for entry in targets),
            return_exceptions=True,
        )

    async def terminate_all(self) -> None:
        async with self._lock:
            targets = list(self._entries.values())
        await asyncio.gather(
            *(self._terminate_and_remove(entry) for entry in targets),
            return_exceptions=True,
        )

    async def _authorized_entry(self, session_id: str) -> ProcessSessionEntry:
        scope = current_process_scope()
        if (
            scope is None
            or scope.session_id is None
            or scope.workspace is None
        ):
            raise ProcessSessionError(
                "live_process_context_required",
                "Live process sessions require an active Run, session, and workspace.",
            )
        async with self._lock:
            entry = self._entries.get(session_id)
        if entry is None:
            raise ProcessSessionError(
                "process_session_not_found",
                "The process session does not exist or has already closed.",
            )
        if (
            entry.run_id != scope.run_id
            or entry.conversation_session_id != scope.session_id
            or entry.workspace != scope.workspace
        ):
            raise ProcessSessionError(
                "process_session_scope_mismatch",
                "The process session belongs to a different Run, session, or workspace.",
            )
        return entry

    async def _read_stream(
        self,
        entry: ProcessSessionEntry,
        reader: asyncio.StreamReader | None,
        stream: str,
    ) -> None:
        if reader is None:
            return
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        transcript = (
            entry.stdout_transcript if stream == "stdout" else entry.stderr_transcript
        )
        while True:
            chunk = await reader.read(PROCESS_SESSION_READ_CHUNK_BYTES)
            if not chunk:
                break
            text = decoder.decode(chunk)
            pending = (
                entry.stdout_pending
                if stream == "stdout"
                else entry.stderr_pending
            )
            pending.append(text)
            transcript.append(text)
            entry.activity.set()
        tail = decoder.decode(b"", final=True)
        pending = (
            entry.stdout_pending if stream == "stdout" else entry.stderr_pending
        )
        pending.append(tail)
        transcript.append(tail)
        entry.activity.set()

    async def _wait_for_process(self, entry: ProcessSessionEntry) -> int:
        exit_code = await entry.process.wait()
        entry.activity.set()
        return exit_code

    async def _timeout_session(self, entry: ProcessSessionEntry) -> None:
        try:
            await asyncio.sleep(entry.timeout_seconds)
            if entry.process.returncode is None:
                entry.timed_out = True
                await process_supervisor.terminate(entry.managed)
                entry.activity.set()
        except asyncio.CancelledError:
            return

    async def _evict_idle_session(self, entry: ProcessSessionEntry) -> None:
        try:
            while True:
                remaining = (
                    PROCESS_SESSION_IDLE_SECONDS
                    - (time.monotonic() - entry.last_used_at)
                )
                if remaining <= 0:
                    await self._terminate_and_remove(entry)
                    return
                await asyncio.sleep(remaining)
        except asyncio.CancelledError:
            return

    async def _finish_readers(self, entry: ProcessSessionEntry) -> None:
        tasks = [
            task
            for task in (entry.stdout_task, entry.stderr_task, entry.wait_task)
            if task is not None
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _terminate_and_remove(self, entry: ProcessSessionEntry) -> None:
        await process_supervisor.terminate(entry.managed)
        await self._finish_readers(entry)
        await self._remove_entry(entry)

    async def _remove_entry(self, entry: ProcessSessionEntry) -> None:
        async with self._lock:
            if self._entries.get(entry.session_id) is not entry:
                return
            del self._entries[entry.session_id]
        current = asyncio.current_task()
        background_tasks = (entry.timeout_task, entry.idle_task)
        for task in background_tasks:
            if task is not None and task is not current and not task.done():
                task.cancel()
        await asyncio.gather(
            *(
                task
                for task in background_tasks
                if task is not None and task is not current
            ),
            return_exceptions=True,
        )
        await process_supervisor.unregister(entry.managed)


process_session_manager = ProcessSessionManager()
