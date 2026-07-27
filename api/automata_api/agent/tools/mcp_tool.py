from __future__ import annotations

import hashlib
import re
import time
from typing import Any

from automata_api.agent.mcp.manager import McpConnectionManager
from automata_api.agent.mcp.policy import McpPolicyEngine
from automata_api.agent.mcp.result import (
    mcp_error_tool_result,
    mcp_result_to_tool_result,
    validate_arguments,
)
from automata_api.agent.mcp.schema import McpError, McpToolMetadata
from automata_api.agent.tools._core import ToolResult
from automata_api.agent.tools.base import AgentTool

_NON_NAME_CHARACTERS = re.compile(r"[^a-zA-Z0-9_-]+")
_MULTIPLE_UNDERSCORES = re.compile(r"_+")


def mcp_tool_alias(server_name: str, tool_name: str) -> str:
    server_slug = _slug(server_name, fallback="server")[:12]
    tool_slug = _slug(tool_name, fallback="tool")[:32]
    digest = hashlib.sha256(
        f"mcp\0{server_name}\0{tool_name}".encode("utf-8")
    ).hexdigest()[:8]
    return f"mcp__{server_slug}__{tool_slug}__{digest}"


class McpAgentTool(AgentTool):
    def __init__(
        self,
        *,
        metadata: McpToolMetadata,
        spec: dict[str, Any],
        manager: McpConnectionManager,
        policy: McpPolicyEngine,
    ) -> None:
        self.name = metadata.alias
        self.read_only = metadata.read_only
        self.metadata = metadata
        self._spec = spec
        self._manager = manager
        self._policy = policy

    def spec(self) -> dict[str, Any]:
        return self._spec

    def policy_decision(self, arguments: dict, *, mode: str):
        return self._policy.evaluate(
            tool=self.metadata,
            arguments=arguments,
            mode=mode,
        )

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        return await self.run_in_mode(arguments, mode="act")

    async def run_in_mode(
        self, arguments: dict[str, Any], *, mode: str
    ) -> ToolResult:
        try:
            validate_arguments(self.metadata.input_schema, arguments)
            decision = self._policy.evaluate(
                tool=self.metadata,
                arguments=arguments,
                mode=mode,
            )
            if decision.action == "prompt":
                raise McpError("mcp_approval_required", decision.reason)
            if decision.action == "deny":
                raise McpError(decision.reason, decision.reason)

            started = time.perf_counter()
            result = await self._manager.call_tool(
                self.metadata.server_name,
                self.metadata.original_name,
                arguments,
            )
            return mcp_result_to_tool_result(
                metadata=self.metadata,
                arguments=arguments,
                result=result,
                duration_seconds=time.perf_counter() - started,
            )
        except McpError as error:
            return mcp_error_tool_result(
                metadata=self.metadata,
                arguments=arguments,
                error=error,
            )

    async def run_authorized(
        self, arguments: dict[str, Any], *, mode: str
    ) -> ToolResult:
        del mode
        try:
            validate_arguments(self.metadata.input_schema, arguments)
            started = time.perf_counter()
            result = await self._manager.call_tool(
                self.metadata.server_name,
                self.metadata.original_name,
                arguments,
            )
            return mcp_result_to_tool_result(
                metadata=self.metadata,
                arguments=arguments,
                result=result,
                duration_seconds=time.perf_counter() - started,
            )
        except McpError as error:
            return mcp_error_tool_result(
                metadata=self.metadata,
                arguments=arguments,
                error=error,
            )


def _slug(value: str, *, fallback: str) -> str:
    normalized = _NON_NAME_CHARACTERS.sub("_", value.strip())
    normalized = _MULTIPLE_UNDERSCORES.sub("_", normalized).strip("_")
    return normalized or fallback
