from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TextIO

import anyio
import anyio.lowlevel
import mcp.types as types
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp import StdioServerParameters
from mcp.shared.message import SessionMessage

from automata_api.agent.execution.permissions import CompiledPermissionProfile
from automata_api.agent.execution.process import process_supervisor
from automata_api.agent.execution.sandbox import process_launcher


@asynccontextmanager
async def sandboxed_stdio_client(
    server: StdioServerParameters,
    *,
    profile: CompiledPermissionProfile,
    errlog: TextIO,
    explicit_env_names: tuple[str, ...] = (),
) -> AsyncIterator[
    tuple[
        MemoryObjectReceiveStream[SessionMessage | Exception],
        MemoryObjectSendStream[SessionMessage],
    ]
]:
    read_writer, read_stream = anyio.create_memory_object_stream[
        SessionMessage | Exception
    ](0)
    write_stream, write_reader = anyio.create_memory_object_stream[SessionMessage](0)
    argv, runtime_roots = _stdio_argv(server)
    process = await process_launcher.spawn(
        *argv,
        cwd=server.cwd or profile.workspace_roots[0],
        env=server.env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        profile=profile,
        scope_name="mcp-stdio",
        runtime_roots=runtime_roots,
        explicit_env_names=explicit_env_names,
    )
    managed = await process_supervisor.register(process)

    async def stdout_reader() -> None:
        if process.stdout is None:
            return
        try:
            async with read_writer:
                while line := await process.stdout.readline():
                    try:
                        message = types.JSONRPCMessage.model_validate_json(
                            line.decode(
                                server.encoding,
                                errors=server.encoding_error_handler,
                            )
                        )
                    except Exception as error:
                        await read_writer.send(error)
                        continue
                    await read_writer.send(SessionMessage(message))
        except anyio.ClosedResourceError:
            await anyio.lowlevel.checkpoint()

    async def stdin_writer() -> None:
        if process.stdin is None:
            return
        try:
            async with write_reader:
                async for session_message in write_reader:
                    payload = session_message.message.model_dump_json(
                        by_alias=True,
                        exclude_none=True,
                    )
                    process.stdin.write(
                        (payload + "\n").encode(
                            server.encoding,
                            errors=server.encoding_error_handler,
                        )
                    )
                    await process.stdin.drain()
        except (anyio.ClosedResourceError, BrokenPipeError, ConnectionResetError):
            await anyio.lowlevel.checkpoint()

    async def stderr_reader() -> None:
        if process.stderr is None:
            return
        while chunk := await process.stderr.read(8192):
            errlog.write(
                chunk.decode(
                    server.encoding,
                    errors="replace",
                )
            )
            errlog.flush()

    try:
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(stdout_reader)
            task_group.start_soon(stdin_writer)
            task_group.start_soon(stderr_reader)
            try:
                yield read_stream, write_stream
            finally:
                await write_stream.aclose()
                if process.stdin is not None:
                    process.stdin.close()
                    try:
                        await process.stdin.wait_closed()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                except TimeoutError:
                    await process_supervisor.terminate(managed)
                await read_stream.aclose()
                await read_writer.aclose()
                await write_reader.aclose()
                task_group.cancel_scope.cancel()
    finally:
        await process_supervisor.unregister(managed)


def _stdio_argv(
    server: StdioServerParameters,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    command = shutil.which(server.command) or server.command
    command_path = Path(command)
    runtime_roots: tuple[str, ...] = ()
    if command_path.exists():
        runtime_roots = (str(command_path.resolve().parent),)
    if sys.platform == "win32" and command_path.suffix.lower() in {".bat", ".cmd"}:
        command_line = subprocess.list2cmdline([command, *server.args])
        return (
            (
                os.environ.get("COMSPEC", "cmd.exe"),
                "/d",
                "/s",
                "/c",
                command_line,
            ),
            runtime_roots,
        )
    return (command, *server.args), runtime_roots
