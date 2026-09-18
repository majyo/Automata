"""Ports the turn engine depends on.

The engine is defined against these protocols rather than against the HTTP
client or the storage layer, which is what lets a Run be exercised with
in-memory doubles. The concrete implementation lives in
``agent.adapters.chat_completions`` and delegates the wire details to
``agent.llm``.

The delta and accumulator shapes are intentionally the same concrete types
``agent.llm`` already produces, so an adapter is a translation layer and not
a parallel type system.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from automata_api.agent.llm import (
    AssistantStreamAccumulator,
    LLMStreamDelta,
)


class ModelProvider(Protocol):
    """Streams one model completion for a message list and tool specs.

    ``stream`` yields provider deltas and ``accumulator`` returns a fresh
    collector; the engine owns accumulation so a provider only has to
    translate its own wire format.
    """

    def stream(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]]
    ) -> AsyncIterator[LLMStreamDelta]: ...

    def accumulator(self) -> AssistantStreamAccumulator: ...
