from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from typing import Callable

from automata_api.agent.mcp.client import McpClient, McpSdkClientAdapter
from automata_api.agent.mcp.config import McpServerDefinition
from automata_api.agent.mcp.schema import (
    McpCallResult,
    McpDiscoveryLimits,
    McpError,
    McpToolInfo,
)
from automata_api.observability import observe_span

McpClientFactory = Callable[[McpServerDefinition, str], McpClient]
logger = logging.getLogger(__name__)


class McpConnectionManager:
    def __init__(
        self,
        definitions: tuple[McpServerDefinition, ...],
        workspace: str,
        *,
        limits: McpDiscoveryLimits | None = None,
        client_factory: McpClientFactory = McpSdkClientAdapter,
    ) -> None:
        self.workspace = workspace
        self.limits = limits or McpDiscoveryLimits()
        self._definitions = {definition.name: definition for definition in definitions}
        self._client_factory = client_factory
        self._clients: dict[str, McpClient] = {}
        self._client_locks: dict[str, asyncio.Lock] = {}
        self._tool_cache: dict[str, tuple[McpToolInfo, ...]] = {}
        self._closed = False

    async def __aenter__(self) -> "McpConnectionManager":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close_all()

    async def list_tools(self, server_name: str) -> tuple[McpToolInfo, ...]:
        if server_name in self._tool_cache:
            return self._tool_cache[server_name]
        definition = self._definition(server_name)
        try:
            async with observe_span(
                "mcp.tools.list",
                attributes={"server": server_name},
            ) as list_span:
                async with asyncio.timeout(
                    min(
                        definition.list_timeout_seconds,
                        self.limits.total_timeout_seconds,
                    )
                ):
                    client = await self._client(server_name)
                    tools = await self._list_all_pages(client)
                list_span.set_attributes(tool_count=len(tools))
        except TimeoutError as error:
            raise McpError(
                "mcp_server_unavailable",
                f"MCP server {server_name!r} did not finish discovery in time.",
            ) from error
        except McpError:
            raise
        except Exception as error:
            raise McpError(
                "mcp_server_unavailable",
                f"MCP server {server_name!r} discovery failed: "
                f"{error.__class__.__name__}.",
            ) from error
        self._tool_cache[server_name] = tools
        return tools

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict,
    ) -> McpCallResult:
        definition = self._definition(server_name)
        try:
            async with observe_span(
                "mcp.call",
                attributes={
                    "server": server_name,
                    "tool": tool_name,
                    "argument_count": len(arguments),
                },
            ):
                async with asyncio.timeout(definition.call_timeout_seconds):
                    client = await self._client(server_name)
                    return await client.call_tool(tool_name, arguments)
        except TimeoutError as error:
            raise McpError(
                "mcp_tool_timeout",
                f"MCP tool {server_name}/{tool_name} timed out.",
            ) from error
        except McpError:
            raise
        except Exception as error:
            raise McpError(
                "mcp_call_outcome_unknown",
                f"MCP tool {server_name}/{tool_name} failed after dispatch: "
                f"{error.__class__.__name__}.",
            ) from error

    def invalidate_tools(self, server_name: str) -> None:
        self._tool_cache.pop(server_name, None)

    async def close_all(self) -> None:
        if self._closed:
            return
        self._closed = True
        clients = tuple(self._clients.values())
        self._clients.clear()
        self._tool_cache.clear()
        for client in clients:
            try:
                # SDK transports use task-bound AnyIO cancel scopes.
                await client.aclose()
            except Exception:
                logger.warning("Failed to close MCP client", exc_info=True)

    async def _client(self, server_name: str) -> McpClient:
        if self._closed:
            raise McpError("mcp_server_unavailable", "MCP manager is closed.")
        existing = self._clients.get(server_name)
        if existing is not None:
            return existing
        lock = self._client_locks.setdefault(server_name, asyncio.Lock())
        async with lock:
            existing = self._clients.get(server_name)
            if existing is not None:
                return existing
            definition = self._definition(server_name)
            client = self._client_factory(definition, self.workspace)
            try:
                async with observe_span(
                    "mcp.server.initialize",
                    attributes={"server": server_name},
                ):
                    await client.start()
            except Exception:
                await client.aclose()
                raise
            self._clients[server_name] = client
            return client

    async def _list_all_pages(
        self, client: McpClient
    ) -> tuple[McpToolInfo, ...]:
        cursor: str | None = None
        seen_cursors: set[str] = set()
        tools: list[McpToolInfo] = []
        for _ in range(self.limits.max_pages):
            if cursor is not None:
                if cursor in seen_cursors:
                    raise McpError(
                        "mcp_pagination_cycle",
                        "MCP tools/list returned a repeated cursor.",
                    )
                seen_cursors.add(cursor)
            page = await client.list_tools_page(cursor)
            for tool in page.tools:
                checked = self._bounded_tool(tool)
                tools.append(checked)
                if len(tools) > self.limits.max_tools:
                    raise McpError(
                        "mcp_discovery_limit_exceeded",
                        "MCP server returned too many tools.",
                    )
            cursor = page.next_cursor
            if not cursor:
                return tuple(tools)
        raise McpError(
            "mcp_discovery_limit_exceeded",
            "MCP tools/list exceeded the maximum page count.",
        )

    def _bounded_tool(self, tool: McpToolInfo) -> McpToolInfo:
        schema_bytes = len(
            json.dumps(
                {
                    "input": tool.input_schema,
                    "output": tool.output_schema,
                },
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if schema_bytes > self.limits.max_tool_schema_bytes:
            raise McpError(
                "mcp_discovery_limit_exceeded",
                f"MCP tool {tool.name!r} schema is too large.",
            )
        description = tool.description
        if description is not None:
            description = description[: self.limits.max_description_chars]
        return replace(tool, description=description)

    def _definition(self, server_name: str) -> McpServerDefinition:
        definition = self._definitions.get(server_name)
        if definition is None:
            raise McpError(
                "mcp_config_error",
                f"Unknown MCP server: {server_name}",
            )
        return definition
