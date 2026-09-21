"""Execute act/plan turns using injected resources and session stores."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from automata_api.config import AgentConfigurationError
from automata_api.core.agent.messages import AgentProviderError, ModelTransportError
from automata_api.core.agent.ports import AgentInputChannel
from automata_api.core.agent.resources import EventSender, TurnResourcesFactory
from automata_api.core.agent.runtime import stream_agent_loop, stream_plan_loop
from automata_api.core.agent.types import AgentLoopEvent
from automata_api.core.runs.approval import ApprovalBroker
from automata_api.core.runs.model import CancellationToken, PublicRunError, RunOutcome
from automata_api.core.sessions.ports import (
    ContextStore,
    ConversationStore,
    SessionStore,
)
from automata_api.core.sessions.rules import SessionNotFoundError
from automata_api.core.tools.orchestrator import ToolExecutionOrchestrator
from automata_api.core.tools.permissions import (
    CompiledPermissionProfile,
    PermissionPreset,
)


class TurnService:
    def __init__(
        self,
        *,
        resources: TurnResourcesFactory,
        sessions: SessionStore,
        conversation: ConversationStore,
        context: ContextStore,
    ) -> None:
        self.resources = resources
        self.sessions = sessions
        self.conversation = conversation
        self.context = context

    async def stream_agent_reply(
        self,
        websocket: EventSender,
        session_id: str,
        prompt: str,
        run_id: str,
        cancellation: CancellationToken,
        approval_broker: ApprovalBroker,
        permission_preset: PermissionPreset,
        permission_profile: CompiledPermissionProfile | None = None,
        selected_skills: object = None,
        approved_plan_content: str | None = None,
        approved_plan_id: str | None = None,
        input_channel: AgentInputChannel | None = None,
        input_id: str | None = None,
    ) -> RunOutcome:
        return await self._reply(
            websocket,
            session_id,
            prompt,
            run_id,
            cancellation,
            approval_broker,
            permission_preset,
            permission_profile,
            selected_skills,
            mode="act",
            approved_plan_content=approved_plan_content,
            input_channel=input_channel,
            input_id=input_id,
        )

    async def stream_plan_reply(
        self,
        websocket: EventSender,
        session_id: str,
        prompt: str,
        prompt_message_id: str,
        run_id: str,
        cancellation: CancellationToken,
        approval_broker: ApprovalBroker,
        permission_preset: PermissionPreset,
        permission_profile: CompiledPermissionProfile | None = None,
        selected_skills: object = None,
        input_channel: AgentInputChannel | None = None,
        input_id: str | None = None,
    ) -> RunOutcome:
        return await self._reply(
            websocket,
            session_id,
            prompt,
            run_id,
            cancellation,
            approval_broker,
            permission_preset,
            permission_profile,
            selected_skills,
            mode="plan",
            input_channel=input_channel,
            input_id=input_id,
        )

    async def _reply(
        self,
        sender: EventSender,
        session_id: str,
        prompt: str,
        run_id: str,
        cancellation: CancellationToken,
        approval_broker: ApprovalBroker,
        permission_preset: PermissionPreset,
        permission_profile: CompiledPermissionProfile | None,
        selected_skills: object,
        *,
        mode: str,
        approved_plan_content: str | None = None,
        input_channel: AgentInputChannel | None = None,
        input_id: str | None = None,
    ) -> RunOutcome:
        started: dict[str, Any] = {
            "type": "started",
            "run_id": run_id,
            "session_id": session_id,
            "prompt": prompt,
            "permission_preset": permission_preset,
        }
        if input_id:
            started["input_id"] = input_id
        if mode == "plan":
            started["mode"] = mode
        await sender.send_json(started)
        try:
            session_config = await asyncio.to_thread(
                self.sessions.session_backend_config, session_id
            )
            async with self.resources.open(
                session_id=session_id,
                session_config=session_config,
                mode=mode,
                prompt=prompt,
                selected_skills=selected_skills,
                run_id=run_id,
                permission_profile=permission_profile,
                sender=sender,
            ) as resources:
                options: dict[str, Any] = dict(
                    session_id=session_id,
                    store=self.context,
                    workspace=resources.workspace,
                    workspace_label=resources.workspace_label,
                    router=resources.router,
                    tool_notes=resources.tool_notes,
                    skill_context=resources.skills,
                    run_id=run_id,
                    cancellation=cancellation,
                    provider=resources.provider,
                    settings=resources.settings,
                    orchestrator=ToolExecutionOrchestrator(
                        approval_broker=approval_broker,
                        permission_preset=permission_preset,
                        permission_profile=permission_profile,
                    ),
                    input_channel=input_channel,
                )
                events = (
                    stream_plan_loop(**options)
                    if mode == "plan"
                    else stream_agent_loop(
                        **options, approved_plan_content=approved_plan_content
                    )
                )
                response = await forward_agent_events(
                    session_id=session_id,
                    websocket=sender,
                    events=events,
                    run_id=run_id,
                    conversation=self.conversation,
                )
        except SessionNotFoundError as error:
            raise PublicRunError("session_not_found", str(error)) from error
        except AgentConfigurationError as error:
            raise PublicRunError("agent_configuration_error", str(error)) from error
        except ModelTransportError as error:
            raise PublicRunError(
                "llm_request_failed", f"LLM request failed: {error}"
            ) from error
        except AgentProviderError as error:
            raise PublicRunError("agent_provider_error", str(error)) from error
        cancellation.raise_if_cancelled()
        return RunOutcome(
            response_content=response, plan_content=response if mode == "plan" else None
        )

    async def stream_approved_plan_reply(
        self,
        websocket: EventSender,
        session_id: str,
        plan: dict[str, Any],
        run_id: str,
        cancellation: CancellationToken,
        approval_broker: ApprovalBroker,
        permission_preset: PermissionPreset,
        permission_profile: CompiledPermissionProfile | None = None,
        input_channel: AgentInputChannel | None = None,
    ) -> RunOutcome:
        plan_id = str(plan["id"])
        await websocket.send_json(
            {
                "type": "plan_approved",
                "run_id": run_id,
                "session_id": session_id,
                "plan_id": plan_id,
            }
        )
        return await self.stream_agent_reply(
            websocket=websocket,
            session_id=session_id,
            prompt=f"Approved plan {plan_id}",
            run_id=run_id,
            cancellation=cancellation,
            approval_broker=approval_broker,
            permission_preset=permission_preset,
            permission_profile=permission_profile,
            input_channel=input_channel,
            approved_plan_content=str(plan["content"]),
            approved_plan_id=plan_id,
        )


async def forward_agent_events(
    conversation: ConversationStore,
    session_id: str,
    websocket: EventSender,
    events: AsyncIterator[AgentLoopEvent],
    run_id: str,
) -> str:
    pending_agent_parts: list[str] = []
    final_content = ""
    tool_run_message_ids: dict[str, str] = {}

    async for event in events:
        event = {**event, "run_id": run_id}
        event_type = event["type"]
        if event_type == "token":
            content = event.get("content")
            if isinstance(content, str):
                pending_agent_parts.append(content)
            await websocket.send_json(event)
            continue

        if event_type == "final":
            content = event.get("content")
            if isinstance(content, str):
                final_content = content
            continue

        if event_type == "agent_segment_boundary":
            await save_pending_agent_message(
                conversation, session_id, pending_agent_parts
            )
            continue

        if event_type == "input_applied":
            # A boundary is emitted before final-turn steering. Tool calls
            # already flush the preceding segment; this is a defensive flush
            # for other safe points and keeps visible message order durable.
            await save_pending_agent_message(
                conversation, session_id, pending_agent_parts
            )
            await websocket.send_json(event)
            continue

        if event_type == "tool_call":
            await save_pending_agent_message(
                conversation, session_id, pending_agent_parts
            )
            tool_call_id = tool_call_id_from_event(event)
            message = await run_repository_call(
                conversation.save_tool_run_message,
                session_id=session_id,
                tool_call_id=tool_call_id,
                tool=tool_name_from_event(event),
                arguments=arguments_from_event(event),
            )
            tool_run_message_ids[tool_call_id] = str(message["id"])
            await websocket.send_json(
                {
                    **event,
                    "message_id": str(message["id"]),
                    "arguments": event_text_summary(arguments_from_event(event)),
                }
            )
            continue

        if event_type == "tool_result":
            tool_call_id = tool_call_id_from_event(event)
            message_id = tool_run_message_ids.get(tool_call_id)
            if message_id:
                await run_repository_call(
                    conversation.update_tool_run_result,
                    session_id=session_id,
                    message_id=message_id,
                    success=event.get("success") is not False,
                    content=content_from_event(event),
                )
            else:
                message = await run_repository_call(
                    conversation.save_tool_run_message,
                    session_id=session_id,
                    tool_call_id=tool_call_id,
                    tool=tool_name_from_event(event),
                    arguments="{}",
                )
                await run_repository_call(
                    conversation.update_tool_run_result,
                    session_id=session_id,
                    message_id=str(message["id"]),
                    success=event.get("success") is not False,
                    content=content_from_event(event),
                )
                message_id = str(message["id"])
            content = content_from_event(event)
            await websocket.send_json(
                {
                    **event,
                    "message_id": message_id,
                    "content": event_text_summary(content),
                    "content_truncated": len(content) > 8_000,
                }
            )
            continue

        await websocket.send_json(event)

    return final_content or "".join(pending_agent_parts)


async def save_pending_agent_message(
    conversation: ConversationStore, session_id: str, parts: list[str]
) -> None:
    content = "".join(parts)
    parts.clear()
    if not content.strip():
        return

    await run_repository_call(
        conversation.save_message, session_id=session_id, role="agent", content=content
    )


async def run_repository_call(function, /, *args, **kwargs):
    return await asyncio.to_thread(function, *args, **kwargs)


def tool_call_id_from_event(event: dict[str, Any]) -> str:
    tool_call_id = event.get("tool_call_id")
    return (
        tool_call_id
        if isinstance(tool_call_id, str) and tool_call_id
        else "unknown_tool_call"
    )


def tool_name_from_event(event: dict[str, Any]) -> str:
    tool = event.get("tool")
    return tool if isinstance(tool, str) and tool else "unknown_tool"


def arguments_from_event(event: dict[str, Any]) -> str:
    arguments = event.get("arguments")
    return arguments if isinstance(arguments, str) else "{}"


def content_from_event(event: dict[str, Any]) -> str:
    content = event.get("content")
    return content if isinstance(content, str) else ""


def event_text_summary(content: str, *, limit: int = 8_000) -> str:
    if len(content) <= limit:
        return content
    return f"{content[:limit]}\n… [full result is stored in the linked message]"
