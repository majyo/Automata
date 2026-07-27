from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from mcp import types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server

server = Server("automata-test-mcp")


def lookup_tool() -> types.Tool:
    return types.Tool(
        name="first.lookup",
        description="Lookup an unrelated local identifier",
        inputSchema={"type": "object", "additionalProperties": False},
        annotations=types.ToolAnnotations(readOnlyHint=True),
    )


def echo_tool() -> types.Tool:
    return types.Tool(
        name="records.echo",
        title="Remote record echo",
        description="Echo remote records for the integration test",
        inputSchema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        outputSchema={
            "type": "object",
            "properties": {"echo": {"type": "string"}},
            "required": ["echo"],
            "additionalProperties": False,
        },
        annotations=types.ToolAnnotations(readOnlyHint=True),
    )


@server.list_tools()
async def list_tools(request: types.ListToolsRequest) -> types.ListToolsResult:
    cursor = request.params.cursor if request.params is not None else None
    if cursor is None:
        return types.ListToolsResult(tools=[lookup_tool()], nextCursor="page-2")
    if cursor == "page-2":
        return types.ListToolsResult(tools=[echo_tool()])
    return types.ListToolsResult(tools=[])


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> types.CallToolResult:
    call_log = os.environ.get("FAKE_MCP_CALL_LOG")
    if call_log:
        Path(call_log).write_text(
            json.dumps({"name": name, "arguments": arguments}),
            encoding="utf-8",
        )
    if name != "records.echo":
        return types.CallToolResult(
            content=[types.TextContent(type="text", text="unknown tool")],
            isError=True,
        )
    value = arguments.get("value", "")
    structured = {"echo": value}
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=f"echo:{value}")],
        structuredContent=structured,
    )


async def main() -> None:
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="automata-test-mcp",
                    server_version="1.0",
                    capabilities=server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )
    finally:
        marker = os.environ.get("FAKE_MCP_CLOSED_MARKER")
        if marker:
            Path(marker).write_text("closed\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
