"""Run use cases, independent of the connection that submitted them."""

import asyncio
from typing import Any

from automata_api.core.runs.coordinator import RunCoordinator, RunHandle
from automata_api.core.runs.model import RunOutcome
from automata_api.core.runs.ports import RunStore
from automata_api.core.runs.state import RunNotFoundError
from automata_api.core.runs.turns import TurnService, run_repository_call
from automata_api.core.sessions.ports import ConversationStore


class RunService:
    def __init__(
        self,
        *,
        coordinator: RunCoordinator,
        turns: TurnService,
        conversation: ConversationStore,
        store: RunStore,
    ) -> None:
        self.coordinator = coordinator
        self.turns = turns
        self.conversation = conversation
        self.store = store

    async def start_prompt(
        self, *, session_id: str, prompt: str, mode: str, skills: object = None
    ):
        async def execute(run: RunHandle, user_message: dict[str, Any]) -> RunOutcome:
            await run_repository_call(
                self.conversation.save_context_message,
                session_id=run.session_id,
                message={"role": "user", "content": prompt},
            )
            if mode == "plan":
                return await self.turns.stream_plan_reply(
                    run.event_sink,
                    run.session_id,
                    prompt,
                    str(user_message["id"]),
                    run.run_id,
                    run.cancellation,
                    run.approval_broker,
                    run.permission_preset,
                    run.permission_profile,
                    skills,
                )
            return await self.turns.stream_agent_reply(
                run.event_sink,
                run.session_id,
                prompt,
                run.run_id,
                run.cancellation,
                run.approval_broker,
                run.permission_preset,
                run.permission_profile,
                skills,
            )

        return await self.coordinator.start_prompt(
            session_id=session_id, prompt=prompt, mode=mode, execute=execute
        )

    async def start_plan_execution(
        self, *, session_id: str, plan_id: str, request_id: str, retry: bool
    ):
        async def execute(run: RunHandle, plan: dict[str, Any]) -> RunOutcome:
            return await self.turns.stream_approved_plan_reply(
                run.event_sink,
                run.session_id,
                plan,
                run.run_id,
                run.cancellation,
                run.approval_broker,
                run.permission_preset,
                run.permission_profile,
            )

        return await self.coordinator.start_plan_execution(
            session_id=session_id,
            plan_id=plan_id,
            request_id=request_id,
            retry=retry,
            execute=execute,
        )

    async def active_runs(self):
        return await asyncio.to_thread(
            self.store.list_runs, non_terminal_only=True, limit=500
        )

    async def session_id_for_run(
        self, requested_session_id: str, run_id: str
    ) -> str | None:
        try:
            run = await asyncio.to_thread(self.store.get_run, run_id)
        except RunNotFoundError:
            return None
        session_id = str(run["session_id"])
        if requested_session_id and requested_session_id != session_id:
            return None
        return session_id
