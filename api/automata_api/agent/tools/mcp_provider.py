from __future__ import annotations

import asyncio
import copy
import logging

from jsonschema import Draft202012Validator, SchemaError

from automata_api.agent.mcp.config import (
    McpServerDefinition,
    McpStdioTransportDefinition,
    is_remote_http_transport,
)
from automata_api.agent.mcp.manager import McpConnectionManager
from automata_api.agent.mcp.policy import McpPolicyEngine
from automata_api.agent.mcp.schema import McpError, McpToolInfo, McpToolMetadata
from automata_api.agent.mcp.trust import McpServerGrant, server_fingerprint
from automata_api.agent.tools.mcp_tool import McpAgentTool, mcp_tool_alias
from automata_api.agent.tools.model import (
    ToolDescriptor,
    ToolDiscoveryContext,
    ToolExposure,
)

logger = logging.getLogger(__name__)


class McpToolProvider:
    def __init__(
        self,
        manager: McpConnectionManager,
        servers: tuple[tuple[McpServerDefinition, McpServerGrant], ...],
    ) -> None:
        self._manager = manager
        self._servers = servers

    async def discover(
        self, context: ToolDiscoveryContext
    ) -> tuple[ToolDescriptor, ...]:
        results = await asyncio.gather(
            *(
                self._discover_server(context, definition, grant)
                for definition, grant in self._servers
            ),
            return_exceptions=True,
        )
        descriptors: list[ToolDescriptor] = []
        for (definition, _), result in zip(self._servers, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning(
                    "MCP server %s discovery failed: %s",
                    definition.name,
                    result,
                )
                continue
            descriptors.extend(result)
        return tuple(descriptors)

    async def _discover_server(
        self,
        context: ToolDiscoveryContext,
        definition: McpServerDefinition,
        grant: McpServerGrant,
    ) -> tuple[ToolDescriptor, ...]:
        if grant.connection != "allow":
            return ()
        tools = await self._manager.list_tools(definition.name)
        seen_original_names: set[str] = set()
        descriptors: list[ToolDescriptor] = []
        for tool in tools:
            if tool.name in seen_original_names:
                logger.warning(
                    "MCP server %s returned duplicate tool %s",
                    definition.name,
                    tool.name,
                )
                continue
            seen_original_names.add(tool.name)
            try:
                descriptors.append(
                    self._descriptor_for_tool(
                        context,
                        definition,
                        grant,
                        tool,
                    )
                )
            except McpError as error:
                logger.warning(
                    "MCP tool %s/%s was skipped: %s",
                    definition.name,
                    tool.name,
                    error.message,
                )
        return tuple(descriptors)

    def _descriptor_for_tool(
        self,
        context: ToolDiscoveryContext,
        definition: McpServerDefinition,
        grant: McpServerGrant,
        tool: McpToolInfo,
    ) -> ToolDescriptor:
        input_schema = provider_compatible_schema(tool.input_schema)
        output_schema = (
            provider_compatible_schema(tool.output_schema)
            if tool.output_schema is not None
            else None
        )
        override = definition.tool_overrides.get(tool.name)
        read_only = _read_only(definition, grant, tool, override)
        annotations = tool.annotations
        alias = mcp_tool_alias(definition.name, tool.name)
        metadata = McpToolMetadata(
            alias=alias,
            server_name=definition.name,
            server_fingerprint=server_fingerprint(
                definition,
                context.workspace or self._manager.workspace,
            ),
            original_name=tool.name,
            title=tool.title,
            description=tool.description,
            input_schema=input_schema,
            output_schema=output_schema,
            read_only=read_only,
            destructive=(
                False
                if read_only
                else bool(annotations.get("destructiveHint", True))
            ),
            idempotent=(
                False
                if read_only
                else bool(annotations.get("idempotentHint", False))
            ),
            open_world=bool(annotations.get("openWorldHint", True)),
            remote=is_remote_http_transport(definition),
            credentialed=(
                bool(definition.transport.env)
                if isinstance(definition.transport, McpStdioTransportDefinition)
                else bool(definition.transport.headers)
            ),
            trusted_server=grant.trust == "trusted",
        )
        description = (tool.description or tool.title or tool.name)[:8_000]
        spec = {
            "type": "function",
            "function": {
                "name": alias,
                "description": description,
                "parameters": input_schema,
            },
        }
        exposure = override.exposure if override and override.exposure else definition.default_exposure
        if definition.provenance == "workspace" and exposure == ToolExposure.DIRECT:
            exposure = ToolExposure.DEFERRED
        search_text = " ".join(
            value
            for value in (
                alias,
                definition.name,
                tool.name,
                tool.title or "",
                description,
                _schema_search_text(input_schema),
            )
            if value
        )[: self._manager.limits.max_search_text_chars]
        executor = McpAgentTool(
            metadata=metadata,
            spec=spec,
            manager=self._manager,
            policy=McpPolicyEngine(grant),
        )
        return ToolDescriptor(
            name=alias,
            spec=spec,
            executor=executor,
            read_only=read_only,
            risk="external",
            exposure=exposure,
            source=f"mcp:{definition.name}",
            search_text=search_text,
            identity=f"mcp:{metadata.server_fingerprint}:{tool.name}",
        )


def provider_compatible_schema(schema: dict | None) -> dict:
    converted = copy.deepcopy(schema or {"type": "object"})
    if not isinstance(converted, dict):
        raise McpError("mcp_config_error", "Tool schema must be an object.")
    converted.pop("$schema", None)
    converted.setdefault("type", "object")
    if converted.get("type") != "object":
        raise McpError(
            "mcp_config_error",
            "Tool input/output schema root must have type object.",
        )
    if _contains_external_ref(converted):
        raise McpError(
            "mcp_config_error",
            "External JSON Schema references are not supported.",
        )
    try:
        Draft202012Validator.check_schema(converted)
    except SchemaError as error:
        raise McpError("mcp_config_error", error.message) from error
    return converted


def _read_only(definition, grant, tool, override) -> bool:
    if override is not None and override.read_only is False:
        return False
    if grant.trust != "trusted":
        return False
    if (
        override is not None
        and override.read_only is True
        and definition.provenance != "workspace"
    ):
        return True
    return tool.annotations.get("readOnlyHint") is True


def _contains_external_ref(value) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "$ref" and isinstance(item, str) and not item.startswith("#"):
                return True
            if _contains_external_ref(item):
                return True
    elif isinstance(value, list):
        return any(_contains_external_ref(item) for item in value)
    return False


def _schema_search_text(value) -> str:
    parts: list[str] = []

    def visit(node) -> None:
        if isinstance(node, dict):
            description = node.get("description")
            if isinstance(description, str):
                parts.append(description)
            properties = node.get("properties")
            if isinstance(properties, dict):
                for name, child in properties.items():
                    if isinstance(name, str):
                        parts.append(name)
                    visit(child)
            if "items" in node:
                visit(node["items"])

    visit(value)
    return " ".join(parts)
