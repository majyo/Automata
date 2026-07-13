from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from automata_api.agent.mcp.config import (
    McpServerDefinition,
    McpStdioTransportDefinition,
)
from automata_api.agent.mcp.manager import McpConnectionManager


FIXTURE = Path(__file__).parent / "fixtures" / "fake_mcp_server.py"


def test_official_sdk_stdio_adapter_lists_all_pages_calls_and_closes(tmp_path):
    closed_marker = tmp_path / "closed.txt"
    call_log = tmp_path / "call.json"
    definition = McpServerDefinition(
        name="fake-stdio",
        transport=McpStdioTransportDefinition(
            command=sys.executable,
            args=(str(FIXTURE),),
            cwd=str(tmp_path),
            env={
                "FAKE_MCP_CLOSED_MARKER": str(closed_marker),
                "FAKE_MCP_CALL_LOG": str(call_log),
            },
        ),
        provenance="user",
        source_path="mcp.json",
    )

    async def run():
        async with McpConnectionManager(
            (definition,),
            str(tmp_path),
        ) as manager:
            tools = await manager.list_tools("fake-stdio")
            assert [tool.name for tool in tools] == [
                "first.lookup",
                "records.echo",
            ]
            result = await manager.call_tool(
                "fake-stdio",
                "records.echo",
                {"value": "from-sdk"},
            )
            assert result.is_error is False
            assert result.structured_content == {"echo": "from-sdk"}

    asyncio.run(run())

    assert json.loads(call_log.read_text(encoding="utf-8")) == {
        "name": "records.echo",
        "arguments": {"value": "from-sdk"},
    }
    assert closed_marker.read_text(encoding="utf-8") == "closed\n"
