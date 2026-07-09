from __future__ import annotations

import json
import re
from typing import Any

from automata_api.agent.tools._core import (
    ToolResult,
    json_response,
    parse_tool_arguments,
)
from automata_api.agent.tools.model import ToolDescriptor


TOOL_SEARCH_NAME = "tool_search"
DEFAULT_TOOL_SEARCH_LIMIT = 8
MAX_TOOL_SEARCH_LIMIT = 20


def tool_search_spec() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": TOOL_SEARCH_NAME,
            "description": (
                "Search tools that are available at runtime but not loaded in "
                "the current model-visible tool list. Matching tools are made "
                "available for the next model call in this agent loop."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query describing the tool capability needed.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of matching tools to load.",
                        "minimum": 1,
                        "maximum": MAX_TOOL_SEARCH_LIMIT,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    }


def run_tool_search(
    raw_arguments: str | dict[str, Any] | None,
    *,
    candidates: list[ToolDescriptor],
    mode: str,
    activate,
) -> ToolResult:
    arguments, parse_error = parse_tool_arguments(raw_arguments)
    if parse_error:
        return ToolResult(
            name=TOOL_SEARCH_NAME,
            arguments={},
            content=json_response(
                {
                    "simulated": False,
                    "tool": TOOL_SEARCH_NAME,
                    "ok": False,
                    "error": parse_error,
                }
            ),
            success=False,
        )

    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        return ToolResult(
            name=TOOL_SEARCH_NAME,
            arguments=arguments,
            content=json_response(
                {
                    "simulated": False,
                    "tool": TOOL_SEARCH_NAME,
                    "ok": False,
                    "error": "query must be a non-empty string.",
                }
            ),
            success=False,
        )

    limit = normalized_limit(arguments.get("limit"))
    matches = search_tool_descriptors(query, candidates, limit)
    activated_names = [descriptor.name for descriptor in matches]
    activate(activated_names)

    return ToolResult(
        name=TOOL_SEARCH_NAME,
        arguments=arguments,
        content=json.dumps(
            {
                "simulated": False,
                "tool": TOOL_SEARCH_NAME,
                "ok": True,
                "mode": mode,
                "query": query,
                "activated_tools": activated_names,
                "tools": [search_result_payload(descriptor) for descriptor in matches],
            },
            ensure_ascii=True,
        ),
        success=True,
    )


def search_tool_descriptors(
    query: str, candidates: list[ToolDescriptor], limit: int
) -> list[ToolDescriptor]:
    query_terms = tokenize(query)
    if not query_terms:
        return []

    scored: list[tuple[int, int, ToolDescriptor]] = []
    for index, descriptor in enumerate(candidates):
        haystack = tool_search_text(descriptor)
        haystack_terms = tokenize(haystack)
        score = sum(1 for term in query_terms if term in haystack_terms)
        if query.lower().strip() in haystack.lower():
            score += 3
        if score > 0:
            scored.append((score, -index, descriptor))

    scored.sort(reverse=True)
    return [descriptor for _, _, descriptor in scored[:limit]]


def search_result_payload(descriptor: ToolDescriptor) -> dict[str, Any]:
    function = descriptor.spec.get("function")
    description = ""
    if isinstance(function, dict) and isinstance(function.get("description"), str):
        description = function["description"]

    return {
        "name": descriptor.name,
        "source": descriptor.source,
        "read_only": descriptor.read_only,
        "description": description,
        "spec": descriptor.spec,
    }


def tool_search_text(descriptor: ToolDescriptor) -> str:
    if descriptor.search_text:
        return descriptor.search_text
    return tool_search_text_for_spec(descriptor.name, descriptor.spec)


def tool_search_text_for_spec(name: str, spec: dict[str, Any]) -> str:
    parts = [name, name.replace("_", " ")]
    function = spec.get("function")
    if isinstance(function, dict):
        for key in ("name", "description"):
            value = function.get(key)
            if isinstance(value, str):
                parts.append(value)
        append_schema_text(function.get("parameters"), parts)
    return " ".join(part.strip() for part in parts if part.strip())


def append_schema_text(schema: Any, parts: list[str]) -> None:
    if not isinstance(schema, dict):
        return

    description = schema.get("description")
    if isinstance(description, str):
        parts.append(description)
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for name, child_schema in properties.items():
            if isinstance(name, str):
                parts.append(name)
                parts.append(name.replace("_", " "))
            append_schema_text(child_schema, parts)
    items = schema.get("items")
    append_schema_text(items, parts)


def normalized_limit(value: Any) -> int:
    if isinstance(value, bool):
        return DEFAULT_TOOL_SEARCH_LIMIT
    if isinstance(value, int):
        return min(MAX_TOOL_SEARCH_LIMIT, max(1, value))
    return DEFAULT_TOOL_SEARCH_LIMIT


def tokenize(value: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-zA-Z0-9_]+", value.lower())
        if token
    }
