"""How a stored model-context message came to exist.

The turn engine produces this classification and the conversation store
records it, so the vocabulary sits at the package root: both the agent and
the storage/retrieval layers import it one way and neither depends on the
other. The string values are part of the persisted data shape and must not
change.
"""

from __future__ import annotations

from typing import Any

CONTEXT_SOURCE_CONVERSATION = "conversation"
CONTEXT_SOURCE_SEARCH = "context_search"

ALLOWED_CONTEXT_SOURCES = (
    CONTEXT_SOURCE_CONVERSATION,
    CONTEXT_SOURCE_SEARCH,
)


def is_context_source(value: object) -> bool:
    return isinstance(value, str) and value in ALLOWED_CONTEXT_SOURCES


def source_for_assistant_message(
    message: dict[str, Any], *, search_tool_name: str
) -> str:
    """Classify an assistant message that requested tool calls.

    A turn is only marked as a search turn when *every* requested call is
    the thread-context search tool; a mixed turn is ordinary conversation.
    """
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list) or not tool_calls:
        return CONTEXT_SOURCE_CONVERSATION

    names: list[str] = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            return CONTEXT_SOURCE_CONVERSATION
        function = tool_call.get("function")
        if not isinstance(function, dict):
            return CONTEXT_SOURCE_CONVERSATION
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            return CONTEXT_SOURCE_CONVERSATION
        names.append(name)

    if names and all(name == search_tool_name for name in names):
        return CONTEXT_SOURCE_SEARCH
    return CONTEXT_SOURCE_CONVERSATION


def source_for_tool_result(tool_name: str, *, search_tool_name: str) -> str:
    return (
        CONTEXT_SOURCE_SEARCH
        if tool_name == search_tool_name
        else CONTEXT_SOURCE_CONVERSATION
    )
