import asyncio
import json
import re

from automata_api.agent.mcp.config import (
    McpServerDefinition,
    McpStdioTransportDefinition,
    McpStreamableHttpTransportDefinition,
)
from automata_api.agent.mcp.schema import (
    McpCallResult,
    McpDiscoveryLimits,
    McpToolInfo,
)
from automata_api.agent.mcp.trust import create_grant
from automata_api.agent.tools.mcp_provider import McpToolProvider
from automata_api.agent.tools.mcp_tool import mcp_tool_alias
from automata_api.agent.tools.model import ToolDiscoveryContext, ToolExposure
from automata_api.agent.tools.router import ToolRouter
from automata_api.agent.tools.tool_search import TOOL_SEARCH_NAME


class FakeManager:
    def __init__(self, workspace, tools, result):
        self.workspace = workspace
        self.limits = McpDiscoveryLimits()
        self.tools = tools
        self.result = result
        self.calls = []

    async def list_tools(self, server_name):
        return self.tools

    async def call_tool(self, server_name, tool_name, arguments):
        self.calls.append((server_name, tool_name, arguments))
        return self.result


def definition(*, provenance="user"):
    return McpServerDefinition(
        name="server-with-a-very-long-name",
        transport=McpStdioTransportDefinition(command="fake"),
        provenance=provenance,
        source_path="mcp.json",
        default_exposure=ToolExposure.DEFERRED,
    )


def tool(*, read_only=True, output_schema=None):
    return McpToolInfo(
        name="lookup.tool.with.a.name.that.is.longer.than.the.model.limit",
        title="Lookup",
        description="Search remote records",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        output_schema=output_schema,
        annotations={"readOnlyHint": read_only},
    )


def discover(manager, server, grant):
    provider = McpToolProvider(manager, ((server, grant),))
    return asyncio.run(
        provider.discover(
            ToolDiscoveryContext(
                session_id="session",
                workspace=manager.workspace,
                backend=None,
                mode="act",
            )
        )
    )


def test_provider_builds_deferred_alias_and_dispatches_original_tool(tmp_path):
    server = definition()
    grant = create_grant(
        server,
        str(tmp_path),
        connection="allow",
        trust="trusted",
        default_call_policy="allow",
    )
    manager = FakeManager(
        str(tmp_path),
        (tool(),),
        McpCallResult(
            content=({"type": "text", "text": "record"},),
            structured_content=None,
        ),
    )
    descriptors = discover(manager, server, grant)

    assert len(descriptors) == 1
    descriptor = descriptors[0]
    assert descriptor.exposure == ToolExposure.DEFERRED
    assert descriptor.read_only is True
    assert len(descriptor.name) <= 64
    assert re.fullmatch(r"[a-zA-Z0-9_-]+", descriptor.name)
    router = ToolRouter(descriptors)

    search = asyncio.run(
        router.dispatch(TOOL_SEARCH_NAME, {"query": "remote records"})
    )
    assert json.loads(search.content)["activated_tools"] == [descriptor.name]
    result = asyncio.run(router.dispatch(descriptor.name, {"query": "demo"}))

    assert result.success is True
    assert manager.calls == [
        (server.name, tool().name, {"query": "demo"})
    ]
    assert json.loads(result.content)["text"] == "record"


def test_untrusted_annotation_does_not_enable_plan_mode(tmp_path):
    server = definition(provenance="workspace")
    grant = create_grant(
        server,
        str(tmp_path),
        connection="allow",
        trust="untrusted",
        default_call_policy="allow",
    )
    manager = FakeManager(
        str(tmp_path),
        (tool(read_only=True),),
        McpCallResult(content=()),
    )
    descriptor = discover(manager, server, grant)[0]

    assert descriptor.read_only is False
    router = ToolRouter((descriptor,))
    assert TOOL_SEARCH_NAME not in {
        item["function"]["name"]
        for item in router.model_visible_specs(mode="plan")
    }


def test_prompt_policy_does_not_send_mcp_request(tmp_path):
    server = definition()
    grant = create_grant(
        server,
        str(tmp_path),
        connection="allow",
        trust="trusted",
        default_call_policy="prompt",
    )
    manager = FakeManager(str(tmp_path), (tool(),), McpCallResult(content=()))
    descriptor = discover(manager, server, grant)[0]
    router = ToolRouter((descriptor,))
    asyncio.run(router.dispatch(TOOL_SEARCH_NAME, {"query": "lookup"}))

    result = asyncio.run(router.dispatch(descriptor.name, {"query": "demo"}))

    assert result.success is False
    assert json.loads(result.content)["error"] == "mcp_approval_required"
    assert manager.calls == []


def test_output_schema_is_validated_before_model_result(tmp_path):
    server = definition()
    grant = create_grant(
        server,
        str(tmp_path),
        connection="allow",
        trust="trusted",
        default_call_policy="allow",
    )
    schema = {
        "type": "object",
        "properties": {"count": {"type": "integer"}},
        "required": ["count"],
    }
    manager = FakeManager(
        str(tmp_path),
        (tool(output_schema=schema),),
        McpCallResult(content=(), structured_content={"count": "bad"}),
    )
    descriptor = discover(manager, server, grant)[0]
    router = ToolRouter((descriptor,))
    asyncio.run(router.dispatch(TOOL_SEARCH_NAME, {"query": "lookup"}))

    result = asyncio.run(router.dispatch(descriptor.name, {"query": "demo"}))

    assert result.success is False
    assert json.loads(result.content)["error"] == "mcp_output_schema_error"


def test_input_schema_is_validated_before_request(tmp_path):
    server = definition()
    grant = create_grant(
        server,
        str(tmp_path),
        connection="allow",
        trust="trusted",
        default_call_policy="allow",
    )
    manager = FakeManager(str(tmp_path), (tool(),), McpCallResult(content=()))
    descriptor = discover(manager, server, grant)[0]
    router = ToolRouter((descriptor,))
    asyncio.run(router.dispatch(TOOL_SEARCH_NAME, {"query": "lookup"}))

    result = asyncio.run(router.dispatch(descriptor.name, {}))

    assert result.success is False
    assert json.loads(result.content)["error"] == "mcp_input_schema_error"
    assert manager.calls == []


def test_mcp_error_result_maps_to_failed_tool_result(tmp_path):
    server = definition()
    grant = create_grant(
        server,
        str(tmp_path),
        connection="allow",
        trust="trusted",
        default_call_policy="allow",
    )
    manager = FakeManager(
        str(tmp_path),
        (tool(),),
        McpCallResult(
            content=({"type": "text", "text": "server rejected call"},),
            is_error=True,
        ),
    )
    descriptor = discover(manager, server, grant)[0]
    router = ToolRouter((descriptor,))
    asyncio.run(router.dispatch(TOOL_SEARCH_NAME, {"query": "lookup"}))

    result = asyncio.run(router.dispatch(descriptor.name, {"query": "demo"}))

    assert result.success is False
    assert json.loads(result.content)["is_error"] is True


def test_alias_is_stable_and_hash_distinguishes_truncated_names():
    prefix = "tool-name-that-is-longer-than-thirty-two-characters-"
    first = mcp_tool_alias("server", prefix + "one")
    second = mcp_tool_alias("server", prefix + "two")

    assert first == mcp_tool_alias("server", prefix + "one")
    assert first != second
    assert len(first) <= 64
    assert len(second) <= 64


def test_remote_http_tool_metadata_records_network_and_credentials(tmp_path):
    server = McpServerDefinition(
        name="remote",
        transport=McpStreamableHttpTransportDefinition(
            url="https://mcp.example.test/service",
            headers={"Authorization": "Bearer ${TOKEN}"},
        ),
        provenance="user",
        source_path="mcp.json",
    )
    grant = create_grant(
        server,
        str(tmp_path),
        connection="allow",
        trust="trusted",
        default_call_policy="allow",
    )
    manager = FakeManager(str(tmp_path), (tool(),), McpCallResult(content=()))

    descriptor = discover(manager, server, grant)[0]

    assert descriptor.executor.metadata.remote is True
    assert descriptor.executor.metadata.credentialed is True
