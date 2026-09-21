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

from automata_api.core.tools.constants import PROCESS_OUTPUT_CHUNK_BYTES
from automata_api.core.tools.output_scope import emit_tool_output
from automata_api.core.tools.text_buffer import HeadTailTextBuffer
from automata_api.infrastructure.processes.process import (
    get_process_supervisor,
)
from automata_api.infrastructure.sandbox.launcher import emit_sandbox_event
from automata_api.infrastructure.sandbox.model import SandboxMetadata
from automata_api.infrastructure.sandbox.protocol import (
    SandboxFailure,
    classify_sandbox_failure,
)


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


async def capture_process_output(
    process: Any,
    timeout_seconds: float,
    *,
    stdout_limit: int,
    stderr_limit: int,
    emit_output: bool = True,
) -> CapturedProcessOutput:
    """Read a process to completion under a timeout, bounded per stream."""
    managed = await get_process_supervisor().register(process)
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
            await get_process_supervisor().terminate(managed)
            await asyncio.shield(wait_task)
            exit_code = None
        except asyncio.CancelledError:
            await get_process_supervisor().terminate(managed)
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
        await get_process_supervisor().unregister(managed)


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
