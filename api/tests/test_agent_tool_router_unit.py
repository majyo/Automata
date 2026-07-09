import asyncio
import json
from typing import Any

import pytest

from automata_api.agent.backends.local import LocalBackend
from automata_api.agent.tools import ToolResult
from automata_api.agent.tools.base import AgentTool
from automata_api.agent.tools.model import ToolDescriptor, ToolExposure
from automata_api.agent.tools.providers import descriptor_for_tool
from automata_api.agent.tools.router import ToolRouter
from automata_api.agent.tools.tool_search import TOOL_SEARCH_NAME


class EchoTool(AgentTool):
    def __init__(
        self,
        name: str,
        *,
        description: str,
        read_only: bool = False,
    ) -> None:
        self.name = name
        self.description = description
        self.read_only = read_only

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
            },
        }

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(
            name=self.name,
            arguments=arguments,
            content=json.dumps({"ok": True, "tool": self.name, "arguments": arguments}),
            success=True,
        )


def descriptor(
    name: str,
    *,
    exposure: ToolExposure = ToolExposure.DIRECT,
    read_only: bool = False,
    description: str | None = None,
) -> ToolDescriptor:
    tool = EchoTool(
        name,
        read_only=read_only,
        description=description or f"{name} test tool",
    )
    return descriptor_for_tool(tool, exposure=exposure, source="unit-test")


def tool_names(specs: list[dict[str, Any]]) -> set[str]:
    return {spec["function"]["name"] for spec in specs}


def test_backend_tools_are_direct_and_keep_existing_specs(tmp_path):
    backend = LocalBackend(str(tmp_path))
    router = ToolRouter.from_backend(backend)

    specs = router.model_visible_specs(mode="act")
    backend_specs = [tool.spec() for tool in backend.tools()]

    assert specs == backend_specs
    assert TOOL_SEARCH_NAME not in tool_names(specs)


def test_router_rejects_duplicate_tool_names():
    duplicate = descriptor("duplicate")

    with pytest.raises(ValueError, match="Duplicate tool registered"):
        ToolRouter([duplicate, duplicate])


def test_deferred_tool_is_loaded_by_tool_search_before_dispatch():
    router = ToolRouter(
        [
            descriptor("read_direct", read_only=True),
            descriptor(
                "calendar_lookup",
                exposure=ToolExposure.DEFERRED,
                read_only=True,
                description="Search calendar events and meetings",
            ),
            descriptor("hidden_debug", exposure=ToolExposure.HIDDEN, read_only=True),
        ]
    )

    assert tool_names(router.model_visible_specs(mode="act")) == {
        "read_direct",
        TOOL_SEARCH_NAME,
    }

    not_loaded = asyncio.run(
        router.dispatch(
            "calendar_lookup", {"value": "today"}, mode="act"
        )
    )
    assert json.loads(not_loaded.content)["error"] == "tool_not_loaded"

    result = asyncio.run(
        router.dispatch(
            TOOL_SEARCH_NAME,
            {"query": "calendar meeting", "limit": 3},
            mode="act",
        )
    )
    payload = json.loads(result.content)
    assert result.success is True
    assert payload["activated_tools"] == ["calendar_lookup"]
    assert tool_names(router.model_visible_specs(mode="act")) == {
        "read_direct",
        "calendar_lookup",
    }

    loaded = asyncio.run(
        router.dispatch(
            "calendar_lookup", {"value": "today"}, mode="act"
        )
    )
    assert loaded.success is True
    assert json.loads(loaded.content)["tool"] == "calendar_lookup"
    assert "hidden_debug" not in tool_names(router.model_visible_specs(mode="act"))


def test_plan_mode_filters_mutating_tools_and_searches_only_read_only_deferred():
    router = ToolRouter(
        [
            descriptor("read_direct", read_only=True),
            descriptor("write_direct", read_only=False),
            descriptor(
                "read_deferred",
                exposure=ToolExposure.DEFERRED,
                read_only=True,
                description="Deferred readonly lookup",
            ),
            descriptor(
                "write_deferred",
                exposure=ToolExposure.DEFERRED,
                read_only=False,
                description="Deferred mutating writer",
            ),
        ]
    )

    assert tool_names(router.model_visible_specs(mode="plan")) == {
        "read_direct",
        TOOL_SEARCH_NAME,
    }

    blocked = asyncio.run(
        router.dispatch("write_direct", {"value": "no"}, mode="plan")
    )
    blocked_payload = json.loads(blocked.content)
    assert blocked.success is False
    assert blocked_payload["error"] == "blocked_by_plan_mode"
    assert blocked_payload["allowed_tools"] == ["read_direct", TOOL_SEARCH_NAME]

    search = asyncio.run(
        router.dispatch(TOOL_SEARCH_NAME, {"query": "deferred"}, mode="plan")
    )
    search_payload = json.loads(search.content)
    assert search_payload["activated_tools"] == ["read_deferred"]
    assert tool_names(router.model_visible_specs(mode="plan")) == {
        "read_direct",
        "read_deferred",
    }
    assert "write_deferred" not in router.allowed_names(mode="plan")
