"""Tool result and tool-call value objects.

``ToolResult`` is what every tool returns; it deliberately carries plain
data (a JSON string, a boolean, an optional code) rather than a
workspace or process object, which is what lets the workspace and
execution layers stay free of tool dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolResult:
    """The outcome of one tool call, as seen by the model and the loop."""

    name: str
    arguments: dict[str, Any]
    content: str
    success: bool
    error_code: str | None = None
    sandbox: dict[str, Any] | None = None
