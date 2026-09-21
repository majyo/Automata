"""HTTP/SSE implementation of the core ModelProvider port."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from automata_api.core.agent.messages import (
    AssistantStreamAccumulator,
    LLMStreamDelta,
    ModelTransportError,
)
from automata_api.infrastructure.llm import client as llm


class ChatCompletionsProvider:
    async def stream(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]]
    ) -> AsyncIterator[LLMStreamDelta]:
        try:
            async for delta in llm.stream_chat_completion(messages, tools=tools):
                yield delta
        except httpx.RequestError as error:
            raise ModelTransportError(error.__class__.__name__) from error

    def accumulator(self) -> AssistantStreamAccumulator:
        return AssistantStreamAccumulator()

    async def complete(
        self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]] | None
    ) -> dict[str, Any]:
        try:
            return await llm.create_llm_response(messages, tools=tools)
        except httpx.RequestError as error:
            raise ModelTransportError(error.__class__.__name__) from error
