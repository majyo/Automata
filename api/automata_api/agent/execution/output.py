"""Bounded process output capture.

Owned by the execution layer: it understands processes, streams and
sandbox metadata, and returns plain value objects. It never constructs a
tool result, which is what keeps ``execution`` independent of ``tools``.
"""

from __future__ import annotations

import asyncio
import codecs
from dataclasses import dataclass
from typing import Any

from automata_api.agent.execution.process import process_supervisor
from automata_api.agent.execution.sandbox.launcher import emit_sandbox_event
from automata_api.agent.execution.sandbox.model import SandboxMetadata
from automata_api.agent.execution.sandbox.protocol import (
    SandboxFailure,
    classify_sandbox_failure,
)
from automata_api.agent.execution.tool_output import emit_tool_output
from automata_api.agent.tools.constants import PROCESS_OUTPUT_CHUNK_BYTES


@dataclass(frozen=True)
class CapturedStream:
    """One captured stream: bounded text plus what the process produced."""

    text: str
    truncated: bool
    bytes_seen: int


@dataclass(frozen=True)
class CapturedProcessOutput:
    """Everything a tool needs to describe a finished process."""

    stdout: CapturedStream
    stderr: CapturedStream
    exit_code: int | None
    timed_out: bool
    sandbox: SandboxMetadata | None
    sandbox_failure: SandboxFailure | None


class HeadTailTextBuffer:
    """Keeps the head and tail of a stream, marking the elision.

    Process output is most useful at both ends (a banner and the final
    error), so truncation keeps half the budget for each rather than
    dropping the tail.
    """

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

    def _marker(self) -> str:
        if self.max_chars < len(self._MARKER) + 2:
            return ""
        return self._MARKER

    def _tail_limit(self) -> int:
        available = max(0, self.max_chars - len(self._marker()))
        return available - available // 2


async def capture_process_output(
    process: Any,
    timeout_seconds: float,
    *,
    stdout_limit: int,
    stderr_limit: int,
    emit_output: bool = True,
) -> CapturedProcessOutput:
    """Read a process to completion under a timeout, bounded per stream."""
    managed = await process_supervisor.register(process)
    try:
        stdout_task = asyncio.create_task(
            read_limited_stream(
                process.stdout,
                stdout_limit,
                stream_name="stdout" if emit_output else None,
            )
        )
        stderr_task = asyncio.create_task(
            read_limited_stream(
                process.stderr,
                stderr_limit,
                stream_name="stderr" if emit_output else None,
            )
        )
        wait_task = asyncio.create_task(process.wait())
        timed_out = False

        try:
            exit_code = await asyncio.wait_for(
                asyncio.shield(wait_task), timeout=timeout_seconds
            )
        except TimeoutError:
            timed_out = True
            await process_supervisor.terminate(managed)
            await asyncio.shield(wait_task)
            exit_code = None
        except asyncio.CancelledError:
            await process_supervisor.terminate(managed)
            await asyncio.gather(
                wait_task, stdout_task, stderr_task, return_exceptions=True
            )
            raise

        stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
        metadata = getattr(process, "automata_sandbox", None)
        if not isinstance(metadata, SandboxMetadata):
            metadata = None
        sandbox_failure = classify_sandbox_failure(
            exit_code=exit_code,
            stderr=stderr.text,
            metadata=metadata,
        )
        if sandbox_failure is not None:
            await emit_sandbox_event(
                {
                    "type": "sandbox_denied",
                    "backend": (
                        metadata.backend if metadata is not None else "unknown"
                    ),
                    "profile_hash": (
                        metadata.profile_hash if metadata is not None else None
                    ),
                    "attempt": metadata.attempt if metadata is not None else 1,
                    "error_code": sandbox_failure.code,
                }
            )
        return CapturedProcessOutput(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timed_out=timed_out,
            sandbox=metadata,
            sandbox_failure=sandbox_failure,
        )
    finally:
        await process_supervisor.unregister(managed)


async def read_limited_stream(
    reader: asyncio.StreamReader | None,
    max_chars: int,
    *,
    chunk_size: int = PROCESS_OUTPUT_CHUNK_BYTES,
    stream_name: str | None = None,
) -> CapturedStream:
    """Read one stream to EOF, emitting deltas and bounding retained text."""
    if reader is None:
        return CapturedStream(text="", truncated=False, bytes_seen=0)

    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    buffer = HeadTailTextBuffer(max_chars)
    bytes_seen = 0

    while True:
        chunk = await reader.read(chunk_size)
        if not chunk:
            break

        bytes_seen += len(chunk)
        text = decoder.decode(chunk)
        if stream_name in {"stdout", "stderr"}:
            await emit_tool_output(stream_name, text)  # type: ignore[arg-type]
        buffer.append(text)

    tail = decoder.decode(b"", final=True)
    if stream_name in {"stdout", "stderr"}:
        await emit_tool_output(stream_name, tail)  # type: ignore[arg-type]
    buffer.append(tail)

    return CapturedStream(
        text=buffer.text,
        truncated=buffer.truncated,
        bytes_seen=bytes_seen,
    )


def append_limited_text(
    parts: list[str], text: str, max_chars: int, chars_kept: int
) -> tuple[int, bool]:
    """Append ``text`` to ``parts`` while respecting a character budget."""
    if not text:
        return chars_kept, False

    remaining = max_chars - chars_kept
    if remaining <= 0:
        return chars_kept, True

    if len(text) <= remaining:
        parts.append(text)
        return chars_kept + len(text), False

    parts.append(text[:remaining])
    return max_chars, True
