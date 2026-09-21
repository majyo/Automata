"""Application bridge between persisted Run inputs and the agent loop."""

from __future__ import annotations

import asyncio
from typing import Any

from automata_api.core.agent.ports import AgentInput
from automata_api.core.runs.model import RunInputGate
from automata_api.core.runs.ports import RunStore
from automata_api.core.sessions.ports import ContextStore, ConversationStore


class RunInputChannel:
    """Delivers steering inputs at agent-loop safe points.

    The channel claims inputs through the RunStore, persists the provider
    context through ContextStore, and only then exposes the input to the
    runtime. The visible user message is written at that same safe point so
    message ordering remains consistent with streamed assistant segments.
    """

    def __init__(
        self,
        *,
        run_id: str,
        session_id: str,
        gate: RunInputGate,
        store: RunStore,
        conversation: ConversationStore,
        context: ContextStore,
    ) -> None:
        self.run_id = run_id
        self.session_id = session_id
        self.gate = gate
        self.store = store
        self.conversation = conversation
        self.context = context

    async def take_steering(self) -> list[AgentInput]:
        rows = await self.gate.claim(
            lambda: asyncio.to_thread(self.store.claim_steering_inputs, self.run_id)
        )
        return [agent_input_from_row(row) for row in rows]

    async def seal_and_take_steering(self) -> list[AgentInput]:
        rows = await self.gate.claim(
            lambda: asyncio.to_thread(self.store.claim_steering_inputs, self.run_id),
            seal=True,
        )
        return [agent_input_from_row(row) for row in rows]

    async def apply_steering(self, item: AgentInput) -> AgentInput:
        await asyncio.to_thread(
            self.context.save_context_message,
            self.session_id,
            {"role": "user", "content": item.prompt},
        )
        message = await asyncio.to_thread(
            self.conversation.save_message,
            self.session_id,
            "user",
            item.prompt,
            metadata={
                "input_id": item.input_id,
                "delivery": "steer",
                "run_id": self.run_id,
            },
        )
        applied = await asyncio.to_thread(
            self.store.mark_input_applied,
            item.input_id,
            run_id=self.run_id,
            message_id=str(message["id"]),
        )
        return AgentInput(
            input_id=item.input_id,
            session_id=item.session_id,
            prompt=item.prompt,
            mode=item.mode,
            delivery="steer",
            message_id=str(applied.get("message_id") or message["id"]),
        )


def agent_input_from_row(row: dict[str, Any]) -> AgentInput:
    mode = row.get("mode")
    return AgentInput(
        input_id=str(row["id"]),
        session_id=str(row["session_id"]),
        prompt=str(row["prompt"]),
        mode="plan" if mode == "plan" else "act",
        delivery="steer",
    )


__all__ = ["RunInputChannel"]
