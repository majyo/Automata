"""Chat-completions model adapter and provider message conversion.

Two responsibilities live here on purpose, because they are the two halves
of the same boundary: turning the engine's internal messages into the
provider payload, and turning the provider's stream back into internal
deltas.

The adapter delegates to :mod:`automata_api.agent.llm`, which owns the
HTTP/SSE details. Delegation is dynamic (the module is looked up at call
time) so the existing ``monkeypatch.setattr(llm, "stream_chat_completion",
...)`` seam used across the test suite keeps working untouched.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from automata_api.agent import llm
from automata_api.agent.llm import AssistantStreamAccumulator, LLMStreamDelta


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


def tool_result_for_provider(
    tool_call: dict[str, Any], result: Any
) -> dict[str, Any]:
    """Convert a tool result into the provider's tool message shape."""
    call_id = tool_call.get("id")
    return {
        "role": "tool",
        "tool_call_id": call_id if isinstance(call_id, str) else "",
        "content": result.content,
    }


class ChatCompletionsProvider:
    """The default :class:`~automata_api.agent.ports.ModelProvider`."""

    def stream(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]]
    ) -> AsyncIterator[LLMStreamDelta]:
        return llm.stream_chat_completion(messages, tools=tools)

    def accumulator(self) -> AssistantStreamAccumulator:
        return llm.AssistantStreamAccumulator()


default_model_provider = ChatCompletionsProvider()
