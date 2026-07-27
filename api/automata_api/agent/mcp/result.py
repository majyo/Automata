from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, SchemaError, ValidationError

from automata_api.agent.mcp.schema import (
    McpCallResult,
    McpError,
    McpToolMetadata,
)
from automata_api.agent.tools._core import ToolResult

MAX_TEXT_CHARS = 64_000
MAX_STRUCTURED_CHARS = 32_000
MAX_CONTENT_BLOCKS = 64
ALLOWED_RESOURCE_SCHEMES = {"file", "http", "https"}


def mcp_result_to_tool_result(
    *,
    metadata: McpToolMetadata,
    arguments: dict[str, Any],
    result: McpCallResult,
    duration_seconds: float,
) -> ToolResult:
    structured_content, structured_truncated = _validated_structured_content(
        metadata.output_schema,
        result.structured_content,
    )
    content, text, content_truncated = _bounded_content(result.content)
    payload = {
        "simulated": False,
        "ok": not result.is_error,
        "tool": metadata.alias,
        "source": f"mcp:{metadata.server_name}",
        "server": metadata.server_name,
        "mcp_tool": metadata.original_name,
        "duration_seconds": round(duration_seconds, 6),
        "is_error": result.is_error,
        "text": text,
        "content": content,
        "structured_content": structured_content,
        "truncated": content_truncated or structured_truncated,
    }
    return ToolResult(
        name=metadata.alias,
        arguments=arguments,
        content=json.dumps(payload, ensure_ascii=True),
        success=not result.is_error,
    )


def mcp_error_tool_result(
    *,
    metadata: McpToolMetadata,
    arguments: dict[str, Any],
    error: McpError,
) -> ToolResult:
    return ToolResult(
        name=metadata.alias,
        arguments=arguments,
        content=json.dumps(
            {
                "simulated": False,
                "ok": False,
                "tool": metadata.alias,
                "source": f"mcp:{metadata.server_name}",
                "server": metadata.server_name,
                "mcp_tool": metadata.original_name,
                "error": error.code,
                "message": error.message,
            },
            ensure_ascii=True,
        ),
        success=False,
    )


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    try:
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(arguments)
    except (SchemaError, ValidationError) as error:
        raise McpError("mcp_input_schema_error", str(error.message)) from error


def _validated_structured_content(
    schema: dict[str, Any] | None,
    value: Any,
) -> tuple[Any, bool]:
    if schema is not None:
        if value is None:
            raise McpError(
                "mcp_output_schema_error",
                "MCP tool declared outputSchema but returned no structuredContent.",
            )
        try:
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(value)
        except (SchemaError, ValidationError) as error:
            raise McpError("mcp_output_schema_error", str(error.message)) from error
    if value is None:
        return None, False
    serialized = json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    if len(serialized) <= MAX_STRUCTURED_CHARS:
        return value, False
    return {
        "truncated": True,
        "preview": serialized[:MAX_STRUCTURED_CHARS],
    }, True


def _bounded_content(
    blocks: tuple[dict[str, Any], ...]
) -> tuple[list[dict[str, Any]], str, bool]:
    bounded: list[dict[str, Any]] = []
    text_parts: list[str] = []
    text_budget = MAX_TEXT_CHARS
    truncated = len(blocks) > MAX_CONTENT_BLOCKS
    for block in blocks[:MAX_CONTENT_BLOCKS]:
        block_type = block.get("type")
        if block_type == "text":
            value = block.get("text")
            if not isinstance(value, str):
                continue
            clipped = value[:text_budget]
            text_budget -= len(clipped)
            text_parts.append(clipped)
            bounded.append({"type": "text", "text": clipped})
            truncated = truncated or len(clipped) < len(value)
            continue
        if block_type in {"image", "audio"}:
            data = block.get("data")
            bounded.append(
                {
                    "type": block_type,
                    "mimeType": block.get("mimeType"),
                    "data_chars": len(data) if isinstance(data, str) else 0,
                    "data_omitted": True,
                }
            )
            truncated = True
            continue
        if block_type == "resource_link":
            uri = block.get("uri")
            if not _safe_resource_uri(uri):
                continue
            bounded.append(
                {
                    key: block.get(key)
                    for key in ("type", "uri", "name", "description", "mimeType")
                    if block.get(key) is not None
                }
            )
            continue
        if block_type == "resource":
            resource = block.get("resource")
            if not isinstance(resource, dict) or not _safe_resource_uri(resource.get("uri")):
                continue
            metadata = {
                key: resource.get(key)
                for key in ("uri", "mimeType")
                if resource.get(key) is not None
            }
            embedded_text = resource.get("text")
            if isinstance(embedded_text, str) and text_budget > 0:
                clipped = embedded_text[:text_budget]
                text_budget -= len(clipped)
                metadata["text"] = clipped
                text_parts.append(clipped)
                truncated = truncated or len(clipped) < len(embedded_text)
            if "blob" in resource:
                metadata["blob_omitted"] = True
                truncated = True
            bounded.append({"type": "resource", "resource": metadata})
    return bounded, "\n".join(text_parts), truncated


def _safe_resource_uri(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    return urlparse(value).scheme.lower() in ALLOWED_RESOURCE_SCHEMES
