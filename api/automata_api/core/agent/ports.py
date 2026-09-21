"""Model capabilities owned by the turn engine.

Infrastructure adapters translate wire responses to core message types.
Tests can supply an in-memory provider without HTTP or API credentials.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from automata_api.core.agent.messages import AssistantStreamAccumulator, LLMStreamDelta


@dataclass(frozen=True)
class AgentInput:
    """A steering input that has been claimed for the current Run."""

    input_id: str
    session_id: str
    prompt: str
    mode: Literal["act", "plan"]
    delivery: Literal["steer"]
    message_id: str | None = None


class AgentInputChannel(Protocol):
    """Safe-boundary input channel owned by the Run/application layer."""

    async def take_steering(self) -> list[AgentInput]: ...

    async def seal_and_take_steering(self) -> list[AgentInput]: ...

    async def apply_steering(self, item: AgentInput) -> AgentInput: ...


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
