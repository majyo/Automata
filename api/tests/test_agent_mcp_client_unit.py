import asyncio

import pytest

from automata_api.agent.mcp.config import (
    McpServerDefinition,
    McpStdioTransportDefinition,
)
from automata_api.agent.mcp.manager import McpConnectionManager
from automata_api.agent.mcp.schema import (
    McpCallResult,
    McpDiscoveryLimits,
    McpError,
    McpListToolsPage,
    McpToolInfo,
)


def definition():
    return McpServerDefinition(
        name="fake",
        transport=McpStdioTransportDefinition(command="fake"),
        provenance="user",
        source_path="mcp.json",
    )


def tool(name):
    return McpToolInfo(
        name=name,
        title=None,
        description=f"Tool {name}",
        input_schema={"type": "object"},
        output_schema=None,
    )


class FakeClient:
    def __init__(self, pages, *, call_error=None):
        self.pages = pages
        self.call_error = call_error
        self.started = 0
        self.closed = 0
        self.calls = []

    async def start(self):
        self.started += 1

    async def list_tools_page(self, cursor):
        return self.pages[cursor]

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.call_error:
            raise self.call_error
        return McpCallResult(content=({"type": "text", "text": "ok"},))

    async def aclose(self):
        self.closed += 1


def test_manager_reads_all_pages_caches_and_closes(tmp_path):
    fake = FakeClient(
        {
            None: McpListToolsPage((tool("one"),), "next"),
            "next": McpListToolsPage((tool("two"),), None),
        }
    )

    async def run():
        async with McpConnectionManager(
            (definition(),),
            str(tmp_path),
            client_factory=lambda _definition, _workspace: fake,
        ) as manager:
            first = await manager.list_tools("fake")
            second = await manager.list_tools("fake")
            assert [item.name for item in first] == ["one", "two"]
            assert second is first

    asyncio.run(run())
    assert fake.started == 1
    assert fake.closed == 1


def test_manager_rejects_pagination_cycle(tmp_path):
    fake = FakeClient(
        {
            None: McpListToolsPage((tool("one"),), "repeat"),
            "repeat": McpListToolsPage((tool("two"),), "repeat"),
        }
    )

    async def run():
        async with McpConnectionManager(
            (definition(),),
            str(tmp_path),
            client_factory=lambda _definition, _workspace: fake,
        ) as manager:
            with pytest.raises(McpError) as captured:
                await manager.list_tools("fake")
            assert captured.value.code == "mcp_pagination_cycle"

    asyncio.run(run())
    assert fake.closed == 1


def test_manager_enforces_tool_limit(tmp_path):
    fake = FakeClient(
        {None: McpListToolsPage((tool("one"), tool("two")), None)}
    )

    async def run():
        async with McpConnectionManager(
            (definition(),),
            str(tmp_path),
            limits=McpDiscoveryLimits(max_tools=1),
            client_factory=lambda _definition, _workspace: fake,
        ) as manager:
            with pytest.raises(McpError) as captured:
                await manager.list_tools("fake")
            assert captured.value.code == "mcp_discovery_limit_exceeded"

    asyncio.run(run())
    assert fake.closed == 1


def test_manager_does_not_retry_unknown_tool_call_outcome(tmp_path):
    fake = FakeClient(
        {None: McpListToolsPage((), None)},
        call_error=RuntimeError("connection lost"),
    )

    async def run():
        async with McpConnectionManager(
            (definition(),),
            str(tmp_path),
            client_factory=lambda _definition, _workspace: fake,
        ) as manager:
            with pytest.raises(McpError) as captured:
                await manager.call_tool("fake", "write", {"value": 1})
            assert captured.value.code == "mcp_call_outcome_unknown"

    asyncio.run(run())
    assert fake.calls == [("write", {"value": 1})]
