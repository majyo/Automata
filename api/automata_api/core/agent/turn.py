"""Pure helpers for one agent turn.

These functions shape the message list the model sees and the results the
engine feeds back. They are deliberately free of I/O, configuration and the
provider, which is what makes them unit-testable on their own and keeps the
turn engine focused on orchestration.
"""

from __future__ import annotations

import json
from typing import Any

from automata_api.core.agent.skills.model import SkillTurnContext
from automata_api.core.tools.models import ToolResult


class EventCollector:
    """Buffers events emitted while a nested step runs.

    Context loading and compression report asynchronously from inside a
    helper; the caller drains the buffer into its own event stream so
    ordering is preserved.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def insert_skill_messages(
    messages: list[dict[str, Any]],
    skill_context: SkillTurnContext | None,
    *,
    index: int,
) -> None:
    """Splice the skill material into the message list at ``index``.

    The index is clamped to ``[1, len(messages)]`` so the system prompt is
    never displaced and an over-large index degrades to "append".
    """
    if skill_context is None or not skill_context.injected_messages:
        return

    bounded_index = max(1, min(index, len(messages)))
    messages[bounded_index:bounded_index] = [
        dict(message) for message in skill_context.injected_messages
    ]


def tool_specs_for_names(
    tools: list[dict[str, Any]], allowed_names: set[str]
) -> list[dict[str, Any]]:
    """Keep only the tool specs whose function name is allowed."""
    return [
        tool
        for tool in tools
        if tool_name(tool) is not None and tool_name(tool) in allowed_names
    ]


def tool_name(tool: dict[str, Any]) -> str | None:
    function = tool.get("function")
    if not isinstance(function, dict):
        return None

    name = function.get("name")
    return name if isinstance(name, str) and name else None


def blocked_tool_result(
    name: str,
    raw_arguments: str | dict[str, Any] | None,
    mode: str,
    allowed_tool_names: set[str],
) -> ToolResult:
    """Report a tool the current mode does not permit.

    Returned as a normal failed result rather than raised, so the model sees
    the rejection and can choose an allowed tool instead of the turn dying.
    """
    arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
    return ToolResult(
        name=name,
        arguments=arguments,
        content=json.dumps(
            {
                "simulated": False,
                "ok": False,
                "tool": name,
                "mode": mode,
                "error": "blocked_by_plan_mode",
                "allowed_tools": sorted(allowed_tool_names),
            },
            ensure_ascii=True,
        ),
        success=False,
    )
