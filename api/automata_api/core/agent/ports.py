"""Model capabilities owned by the turn engine.

Infrastructure adapters translate wire responses to core message types.
Tests can supply an in-memory provider without HTTP or API credentials.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol

from automata_api.core.agent.messages import AssistantStreamAccumulator, LLMStreamDelta


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

    async def complete(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None
    ) -> dict[str, Any]: ...
