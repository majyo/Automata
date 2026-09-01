from __future__ import annotations

from collections.abc import Iterable

from automata_api.agent.tools.base import AgentTool
from automata_api.agent.tools.model import (
    ToolDescriptor,
    ToolDiscoveryContext,
    ToolExposure,
)
from automata_api.agent.tools.thread_context import SearchThreadContextTool
from automata_api.agent.tools.tool_search import tool_search_text_for_spec
from automata_api.agent.types import AgentContextStore


class BackendToolProvider:
    def discover(self, context: ToolDiscoveryContext) -> tuple[ToolDescriptor, ...]:
        if context.backend is None:
            return ()

        return tuple(
            descriptor_for_tool(tool, source=f"backend:{context.backend.kind}")
            for tool in context.backend.tools()
        )


class ContextToolProvider:
    """Provide read-only tools bound to the current Agent context."""

    def __init__(self, store: AgentContextStore) -> None:
        self._store = store

    def discover(self, context: ToolDiscoveryContext) -> tuple[ToolDescriptor, ...]:
        if not context.session_id:
            return ()

        return (
            descriptor_for_tool(
                SearchThreadContextTool(
                    session_id=context.session_id,
                    store=self._store,
                ),
                source="builtin:thread_context",
            ),
        )


class StaticToolProvider:
    def __init__(self, descriptors: Iterable[ToolDescriptor]) -> None:
        self._descriptors = tuple(descriptors)

    def discover(self, context: ToolDiscoveryContext) -> tuple[ToolDescriptor, ...]:
        return self._descriptors


def descriptor_for_tool(
    tool: AgentTool,
    *,
    exposure: ToolExposure = ToolExposure.DIRECT,
    source: str = "static",
    search_text: str | None = None,
) -> ToolDescriptor:
    spec = tool.spec()
    return ToolDescriptor(
        name=tool.name,
        spec=spec,
        executor=tool,
        read_only=tool.read_only,
        risk=tool_risk(tool),
        exposure=exposure,
        source=source,
        search_text=search_text or tool_search_text_for_spec(tool.name, spec),
    )


def tool_risk(tool: AgentTool):
    if tool.read_only:
        return "read"
    if tool.name in {"exec_command", "write_stdin", "run_bash", "run_powershell"}:
        return "command"
    return "write"
