"""Run use cases, independent of the connection that submitted them."""

import asyncio
import uuid
from typing import Any

from automata_api.core.runs.coordinator import RunCoordinator, RunHandle
from automata_api.core.runs.inputs import RunInputChannel
from automata_api.core.runs.model import PromptSubmission, RunOutcome
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
        self,
        *,
        session_id: str,
        prompt: str,
        mode: str,
        skills: object = None,
        delivery: str = "new",
        run_id: str = "",
        request_id: str | None = None,
    ) -> PromptSubmission | tuple[dict[str, Any], dict[str, Any]]:
        resolved_mode = "plan" if mode == "plan" else "act"
        if delivery == "steer":
            if not run_id or not request_id:
                raise ValueError("Steering input requires run_id and request_id.")
            return await self.coordinator.steer_prompt(
                session_id=session_id,
                run_id=run_id,
                prompt=prompt,
                request_id=request_id,
            )

        if delivery == "queue":
            return await self.coordinator.enqueue_prompt(
                session_id=session_id,
                prompt=prompt,
                mode=resolved_mode,
                skills=skills,
                request_id=request_id or uuid.uuid4().hex,
                prompt_executor=self._execute_input,
            )

        async def execute(run: RunHandle, user_message: dict[str, Any]) -> RunOutcome:
            return await self._execute_input(
                run,
                {
                    "id": user_message["id"],
                    "message_id": user_message["id"],
                    "prompt": prompt,
                    "mode": resolved_mode,
                    "skills": skills,
                    "delivery": "new",
                },
            )

        run, user_message = await self.coordinator.start_prompt(
            session_id=session_id,
            prompt=prompt,
            mode=resolved_mode,
            execute=execute,
            prompt_executor=self._execute_input,
        )
        return run, user_message

    async def _execute_input(
        self, run: RunHandle, input_record: dict[str, Any]
    ) -> RunOutcome:
        prompt = str(input_record["prompt"])
        mode = "plan" if input_record.get("mode") == "plan" else "act"
        delivery = str(input_record.get("delivery") or "queue")
        if delivery != "steer":
            await run_repository_call(
                self.conversation.save_context_message,
                session_id=run.session_id,
                message={"role": "user", "content": prompt},
            )

        input_channel = RunInputChannel(
            run_id=run.run_id,
            session_id=run.session_id,
            gate=run.input_gate,
            store=self.store,
            conversation=self.conversation,
            context=self.turns.context,
        )
        message_id = str(
            input_record.get("message_id") or input_record.get("id") or ""
        )
        if mode == "plan":
            return await self.turns.stream_plan_reply(
                run.event_sink,
                run.session_id,
                prompt,
                message_id,
                run.run_id,
                run.cancellation,
                run.approval_broker,
                run.permission_preset,
                run.permission_profile,
                input_record.get("skills"),
                input_channel=input_channel,
                input_id=str(input_record.get("id") or "") or None,
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
            input_record.get("skills"),
            input_channel=input_channel,
            input_id=str(input_record.get("id") or "") or None,
        )

    async def start_plan_execution(
        self, *, session_id: str, plan_id: str, request_id: str, retry: bool
    ):
        async def execute(run: RunHandle, plan: dict[str, Any]) -> RunOutcome:
            input_channel = RunInputChannel(
                run_id=run.run_id,
                session_id=run.session_id,
                gate=run.input_gate,
                store=self.store,
                conversation=self.conversation,
                context=self.turns.context,
            )
            return await self.turns.stream_approved_plan_reply(
                run.event_sink,
                run.session_id,
                plan,
                run.run_id,
                run.cancellation,
                run.approval_broker,
                run.permission_preset,
                run.permission_profile,
                input_channel=input_channel,
            )

        return await self.coordinator.start_plan_execution(
            session_id=session_id,
            plan_id=plan_id,
            request_id=request_id,
            retry=retry,
            execute=execute,
            prompt_executor=self._execute_input,
        )

    async def resume_queued_inputs(self) -> int:
        return await self.coordinator.resume_queued_inputs(self._execute_input)

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
