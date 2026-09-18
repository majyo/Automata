"""The port the turn engine uses to run a builtin tool.

The engine knows how to orchestrate a tool call; it does not know how tools
are executed. ``RunTool`` is the one operation it needs, and
:class:`DefaultToolRunner` supplies the production implementation by
resolving ``automata_api.agent.runtime.run_tool`` at call time.

That indirection is deliberate and temporary: the runtime module is the
seam the existing tests patch, so resolving late keeps those doubles in
effect while the engine's dependency on tool execution stays a named port.
"""

from __future__ import annotations

from typing import Any, Protocol

from automata_api.agent.tools.models import ToolResult


class RunTool(Protocol):
    """Executes one builtin tool by name."""

    async def __call__(
        self, name: str, arguments: str | dict[str, Any] | None, workspace: str
    ) -> ToolResult: ...


class DefaultToolRunner:
    """Resolve the builtin tool runner lazily from the runtime module."""

    async def __call__(
        self, name: str, arguments: str | dict[str, Any] | None, workspace: str
    ) -> ToolResult:
        from automata_api.agent import runtime

        return await runtime.run_tool(name, arguments, workspace)


default_tool_runner: RunTool = DefaultToolRunner()
