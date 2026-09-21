"""Model stream values and accumulation, with no network dependencies."""

from typing import Any, TypedDict


class AgentProviderError(RuntimeError):
    pass


class LLMToolCallDelta(TypedDict, total=False):
    index: int
    id: str
    type: str
    function: dict[str, str]


class LLMStreamDelta(TypedDict, total=False):
    content: str
    reasoning_content: str
    tool_calls: list[LLMToolCallDelta]
    finish_reason: str | None
    usage: dict[str, Any]


def normalize_tool_calls(raw_tool_calls: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_tool_calls, list):
        return []

    normalized: list[dict[str, Any]] = []
    for index, tool_call in enumerate(raw_tool_calls):
        if not isinstance(tool_call, dict):
            continue

        function = tool_call.get("function")
        if not isinstance(function, dict):
            continue

        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            continue

        arguments = function.get("arguments")
        if not isinstance(arguments, str):
            arguments = "{}"

        call_id = tool_call.get("id")
        if not isinstance(call_id, str) or not call_id.strip():
            call_id = f"call_{index}"

        normalized.append(
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments,
                },
            }
        )

    return normalized


class AssistantStreamAccumulator:
    def __init__(self) -> None:
        self._content_parts: list[str] = []
        self._reasoning_parts: list[str] = []
        self._tool_calls: dict[int, dict[str, Any]] = {}

    def add(self, delta: LLMStreamDelta) -> None:
        content = delta.get("content")
        if isinstance(content, str):
            self._content_parts.append(content)

        reasoning_content = delta.get("reasoning_content")
        if isinstance(reasoning_content, str):
            self._reasoning_parts.append(reasoning_content)

        for tool_call_delta in delta.get("tool_calls", []):
            index = tool_call_delta.get("index")
            if not isinstance(index, int):
                continue

            tool_call = self._tool_calls.setdefault(
                index,
                {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                },
            )

            call_id = tool_call_delta.get("id")
            if isinstance(call_id, str) and call_id:
                tool_call["id"] = call_id

            call_type = tool_call_delta.get("type")
            if isinstance(call_type, str) and call_type:
                tool_call["type"] = call_type

            function_delta = tool_call_delta.get("function")
            if not isinstance(function_delta, dict):
                continue

            function = tool_call["function"]
            name = function_delta.get("name")
            if isinstance(name, str) and name:
                function["name"] = f"{function.get('name', '')}{name}"

            arguments = function_delta.get("arguments")
            if isinstance(arguments, str):
                function["arguments"] = f"{function.get('arguments', '')}{arguments}"

    def message(self) -> dict[str, Any]:
        content = "".join(self._content_parts)
        raw_tool_calls = [
            {
                "id": tool_call["id"] or f"call_{index}",
                "type": tool_call.get("type") or "function",
                "function": {
                    "name": tool_call["function"].get("name", ""),
                    "arguments": tool_call["function"].get("arguments", "{}"),
                },
            }
            for index, tool_call in sorted(self._tool_calls.items())
        ]
        message: dict[str, Any] = {
            "role": "assistant",
            "content": content,
            "tool_calls": normalize_tool_calls(raw_tool_calls),
        }

        reasoning_content = "".join(self._reasoning_parts)
        if reasoning_content:
            message["reasoning_content"] = reasoning_content

        return message


def assistant_message_for_provider(message: dict[str, Any]) -> dict[str, Any]:
    """Convert an accumulated assistant message into provider shape.

    ``content`` is always present (``None`` when the turn only called
    tools) because some providers reject a missing field, and tool calls
    and reasoning are only included when they exist.
    """
    content = message.get("content")
    provider_message: dict[str, Any] = {
        "role": "assistant",
        "content": content if isinstance(content, str) and content else None,
    }

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        provider_message["tool_calls"] = tool_calls

    reasoning_content = message.get("reasoning_content")
    if isinstance(reasoning_content, str):
        provider_message["reasoning_content"] = reasoning_content

    return provider_message


def tool_result_for_provider(tool_call: dict[str, Any], result: Any) -> dict[str, Any]:
    """Convert a tool result into the provider's tool message shape."""
    call_id = tool_call.get("id")
    return {
        "role": "tool",
        "tool_call_id": call_id if isinstance(call_id, str) else "",
        "content": result.content,
    }


class ModelTransportError(AgentProviderError):
    """Public transport failure, independent of the HTTP library."""
