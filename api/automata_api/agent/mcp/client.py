from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Protocol

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from automata_api.agent.mcp.config import (
    McpServerDefinition,
    McpStdioTransportDefinition,
    McpStreamableHttpTransportDefinition,
    resolve_stdio_transport,
    resolve_streamable_http_transport,
)
from automata_api.agent.mcp.schema import (
    McpCallResult,
    McpListToolsPage,
    McpToolInfo,
)


class McpClient(Protocol):
    async def start(self) -> None: ...

    async def list_tools_page(self, cursor: str | None) -> McpListToolsPage: ...

    async def call_tool(
        self, name: str, arguments: dict
    ) -> McpCallResult: ...

    async def aclose(self) -> None: ...


class McpSdkClientAdapter:
    def __init__(self, definition: McpServerDefinition, workspace: str) -> None:
        self.definition = definition
        self.workspace = workspace
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._start_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._session is not None:
            return
        async with self._start_lock:
            if self._session is not None:
                return
            stack = AsyncExitStack()
            await stack.__aenter__()
            try:
                read_stream, write_stream = await self._open_transport(stack)
                session = await stack.enter_async_context(
                    ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(
                            seconds=self.definition.call_timeout_seconds
                        ),
                    )
                )
                await session.initialize()
            except BaseException:
                await stack.aclose()
                raise
            self._stack = stack
            self._session = session

    async def _open_transport(self, stack: AsyncExitStack):
        if isinstance(self.definition.transport, McpStdioTransportDefinition):
            transport = resolve_stdio_transport(
                self.definition,
                self.workspace,
            )
            parameters = StdioServerParameters(
                command=transport.command,
                args=list(transport.args),
                env=dict(transport.env),
                cwd=transport.cwd,
            )
            errlog = stack.enter_context(
                open(os.devnull, "w", encoding="utf-8")
            )
            return await stack.enter_async_context(
                stdio_client(parameters, errlog=errlog)
            )

        if isinstance(
            self.definition.transport,
            McpStreamableHttpTransportDefinition,
        ):
            transport = resolve_streamable_http_transport(
                self.definition,
                self.workspace,
            )
            timeout_seconds = self.definition.call_timeout_seconds
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(
                    headers=dict(transport.headers),
                    timeout=httpx.Timeout(
                        connect=min(10.0, timeout_seconds),
                        read=None,
                        write=timeout_seconds,
                        pool=timeout_seconds,
                    ),
                    follow_redirects=False,
                    trust_env=False,
                )
            )
            read_stream, write_stream, _ = await stack.enter_async_context(
                streamable_http_client(
                    transport.url,
                    http_client=http_client,
                    terminate_on_close=True,
                )
            )
            return read_stream, write_stream

        raise TypeError(f"Unsupported MCP transport: {self.definition.transport!r}")

    async def list_tools_page(self, cursor: str | None) -> McpListToolsPage:
        session = self._require_session()
        result = await session.list_tools(cursor=cursor)
        tools: list[McpToolInfo] = []
        for tool in result.tools:
            payload = tool.model_dump(by_alias=True, exclude_none=True)
            input_schema = payload.get("inputSchema")
            output_schema = payload.get("outputSchema")
            annotations = payload.get("annotations")
            tools.append(
                McpToolInfo(
                    name=tool.name,
                    title=payload.get("title") if isinstance(payload.get("title"), str) else None,
                    description=(
                        payload.get("description")
                        if isinstance(payload.get("description"), str)
                        else None
                    ),
                    input_schema=(
                        input_schema if isinstance(input_schema, dict) else {}
                    ),
                    output_schema=(
                        output_schema if isinstance(output_schema, dict) else None
                    ),
                    annotations=(
                        annotations if isinstance(annotations, dict) else {}
                    ),
                )
            )
        return McpListToolsPage(
            tools=tuple(tools),
            next_cursor=result.nextCursor,
        )

    async def call_tool(self, name: str, arguments: dict) -> McpCallResult:
        session = self._require_session()
        result = await session.call_tool(name, arguments=arguments)
        content = tuple(
            block.model_dump(by_alias=True, exclude_none=True)
            for block in result.content
        )
        return McpCallResult(
            content=content,
            structured_content=result.structuredContent,
            is_error=bool(result.isError),
        )

    async def aclose(self) -> None:
        stack = self._stack
        self._stack = None
        self._session = None
        if stack is not None:
            await stack.aclose()

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError("MCP client has not been started")
        return self._session
