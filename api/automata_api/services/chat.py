import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, Protocol

import httpx

from automata_api.agent.backends.factory import (
    BackendConfigurationError,
    create_backend,
)
from automata_api.agent.llm import AgentProviderError
from automata_api.agent.execution.approval import ApprovalBroker
from automata_api.agent.execution.model import (
    CancellationToken,
    PublicRunError,
    RunOutcome,
)
from automata_api.agent.execution.orchestrator import ToolExecutionOrchestrator
from automata_api.agent.mcp.runtime import create_mcp_tool_runtime
from automata_api.agent.runtime import stream_agent_loop, stream_plan_loop
from automata_api.agent.skills.runtime import (
    create_skill_turn_context,
    skill_selections_from_payload,
)
from automata_api.config import AgentConfigurationError
from automata_api.repositories.agent_store import SessionAgentContextStore
from automata_api.repositories.sessions import (
    SessionNotFoundError,
    save_message,
    save_tool_run_message,
    session_backend_config,
    update_tool_run_result,
)
from automata_api.schemas import ChatPayload


class JsonSender(Protocol):
    async def send_json(self, data: Any) -> None: ...


async def receive_payload(websocket) -> ChatPayload:
    message = await websocket.receive_text()
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        return {"type": "invalid"}

    if not isinstance(payload, dict):
        return {"type": "invalid"}

    return payload


async def stream_agent_reply(
    websocket: JsonSender,
    session_id: str,
    prompt: str,
    run_id: str,
    cancellation: CancellationToken,
    approval_broker: ApprovalBroker,
    selected_skills: object = None,
    approved_plan_content: str | None = None,
    approved_plan_id: str | None = None,
) -> RunOutcome:
    await websocket.send_json(
        {
            "type": "started",
            "run_id": run_id,
            "session_id": session_id,
            "prompt": prompt,
        }
    )
    response = ""

    try:
        session_config = await run_repository_call(session_backend_config, session_id)
        backend = create_backend(
            session_config["backend"],
            workspace=session_config["working_directory"],
        )
        async with backend:
            async with create_mcp_tool_runtime(
                backend=backend,
                session_id=session_id,
                workspace=session_config["working_directory"],
                mode="act",
            ) as mcp_runtime:
                await send_mcp_runtime_events(websocket, mcp_runtime, run_id)
                skill_context = await create_skill_turn_context(
                    workspace=session_config["working_directory"],
                    mode="act",
                    prompt=prompt,
                    selected_skills=skill_selections_from_payload(selected_skills),
                    router=mcp_runtime.router,
                )
                await send_skill_runtime_events(websocket, skill_context, run_id)
                response = await forward_agent_events(
                    session_id=session_id,
                    websocket=websocket,
                    events=stream_agent_loop(
                        session_id=session_id,
                        store=SessionAgentContextStore(),
                        workspace=session_config["working_directory"],
                        workspace_label=backend.workspace_label,
                        router=mcp_runtime.router,
                        tool_notes=backend.prompt_notes(),
                        skill_context=skill_context,
                        approved_plan_content=approved_plan_content,
                        run_id=run_id,
                        cancellation=cancellation,
                        orchestrator=ToolExecutionOrchestrator(
                            approval_broker=approval_broker
                        ),
                    ),
                    run_id=run_id,
                )
    except SessionNotFoundError as error:
        raise PublicRunError("session_not_found", str(error)) from error
    except BackendConfigurationError as error:
        raise PublicRunError("backend_configuration_error", str(error)) from error
    except AgentConfigurationError as error:
        raise PublicRunError("agent_configuration_error", str(error)) from error
    except AgentProviderError as error:
        raise PublicRunError("agent_provider_error", str(error)) from error
    except httpx.RequestError as error:
        raise PublicRunError(
            "llm_request_failed",
            f"LLM request failed: {error.__class__.__name__}",
        ) from error

    cancellation.raise_if_cancelled()
    cancellation.raise_if_cancelled()
    return RunOutcome(response_content=response)


async def stream_plan_reply(
    websocket: JsonSender,
    session_id: str,
    prompt: str,
    prompt_message_id: str,
    run_id: str,
    cancellation: CancellationToken,
    approval_broker: ApprovalBroker,
    selected_skills: object = None,
) -> RunOutcome:
    await websocket.send_json(
        {
            "type": "started",
            "run_id": run_id,
            "session_id": session_id,
            "prompt": prompt,
            "mode": "plan",
        }
    )
    response = ""

    try:
        session_config = await run_repository_call(session_backend_config, session_id)
        backend = create_backend(
            session_config["backend"],
            workspace=session_config["working_directory"],
        )
        async with backend:
            async with create_mcp_tool_runtime(
                backend=backend,
                session_id=session_id,
                workspace=session_config["working_directory"],
                mode="plan",
            ) as mcp_runtime:
                await send_mcp_runtime_events(websocket, mcp_runtime, run_id)
                skill_context = await create_skill_turn_context(
                    workspace=session_config["working_directory"],
                    mode="plan",
                    prompt=prompt,
                    selected_skills=skill_selections_from_payload(selected_skills),
                    router=mcp_runtime.router,
                )
                await send_skill_runtime_events(websocket, skill_context, run_id)
                response = await forward_agent_events(
                    session_id=session_id,
                    websocket=websocket,
                    events=stream_plan_loop(
                        session_id=session_id,
                        store=SessionAgentContextStore(),
                        workspace=session_config["working_directory"],
                        workspace_label=backend.workspace_label,
                        router=mcp_runtime.router,
                        tool_notes=backend.prompt_notes(),
                        skill_context=skill_context,
                        run_id=run_id,
                        cancellation=cancellation,
                        orchestrator=ToolExecutionOrchestrator(
                            approval_broker=approval_broker
                        ),
                    ),
                    run_id=run_id,
                )
    except SessionNotFoundError as error:
        raise PublicRunError("session_not_found", str(error)) from error
    except BackendConfigurationError as error:
        raise PublicRunError("backend_configuration_error", str(error)) from error
    except AgentConfigurationError as error:
        raise PublicRunError("agent_configuration_error", str(error)) from error
    except AgentProviderError as error:
        raise PublicRunError("agent_provider_error", str(error)) from error
    except httpx.RequestError as error:
        raise PublicRunError(
            "llm_request_failed",
            f"LLM request failed: {error.__class__.__name__}",
        ) from error

    cancellation.raise_if_cancelled()
    cancellation.raise_if_cancelled()
    return RunOutcome(response_content=response, plan_content=response)


async def stream_approved_plan_reply(
    websocket: JsonSender,
    session_id: str,
    plan: dict[str, Any],
    run_id: str,
    cancellation: CancellationToken,
    approval_broker: ApprovalBroker,
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
    return await stream_agent_reply(
        websocket=websocket,
        session_id=session_id,
        prompt=f"Approved plan {plan_id}",
        run_id=run_id,
        cancellation=cancellation,
        approval_broker=approval_broker,
        approved_plan_content=str(plan["content"]),
        approved_plan_id=plan_id,
    )


async def send_mcp_runtime_events(
    websocket: JsonSender, runtime, run_id: str
) -> None:
    for warning in runtime.warnings:
        await websocket.send_json(
            {
                "type": "mcp_server_status",
                "run_id": run_id,
                "status": "warning",
                "message": warning,
            }
        )
    for candidate in runtime.candidates:
        await websocket.send_json(
            {
                "type": "mcp_server_candidate",
                "run_id": run_id,
                "server": candidate.name,
                "provenance": candidate.provenance,
                "fingerprint": candidate.fingerprint,
            }
        )


async def send_skill_runtime_events(
    websocket: JsonSender, skill_context, run_id: str
) -> None:
    if skill_context.loaded_count:
        await websocket.send_json(
            {
                "type": "skills_loaded",
                "run_id": run_id,
                "count": skill_context.loaded_count,
                "enabled_count": skill_context.enabled_count,
            }
        )
    for warning in skill_context.warnings:
        await websocket.send_json(
            {"type": "skills_warning", "run_id": run_id, "message": warning}
        )
    for skill in skill_context.selected:
        await websocket.send_json(
            {
                "type": "skill_injected",
                "run_id": run_id,
                "name": skill.name,
                "path": str(skill.path),
            }
        )


async def forward_agent_events(
    session_id: str,
    websocket: JsonSender,
    events: AsyncIterator[dict[str, Any]],
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

        if event_type == "tool_call":
            await save_pending_agent_message(session_id, pending_agent_parts)
            tool_call_id = tool_call_id_from_event(event)
            message = await run_repository_call(
                save_tool_run_message,
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
                    "arguments": event_text_summary(
                        arguments_from_event(event)
                    ),
                }
            )
            continue

        if event_type == "tool_result":
            tool_call_id = tool_call_id_from_event(event)
            message_id = tool_run_message_ids.get(tool_call_id)
            if message_id:
                await run_repository_call(
                    update_tool_run_result,
                    session_id=session_id,
                    message_id=message_id,
                    success=event.get("success") is not False,
                    content=content_from_event(event),
                )
            else:
                message = await run_repository_call(
                    save_tool_run_message,
                    session_id=session_id,
                    tool_call_id=tool_call_id,
                    tool=tool_name_from_event(event),
                    arguments="{}",
                )
                await run_repository_call(
                    update_tool_run_result,
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


async def save_pending_agent_message(session_id: str, parts: list[str]) -> None:
    content = "".join(parts)
    parts.clear()
    if not content.strip():
        return

    await run_repository_call(
        save_message, session_id=session_id, role="agent", content=content
    )


async def run_repository_call(function, /, *args, **kwargs):
    return await asyncio.to_thread(function, *args, **kwargs)


def tool_call_id_from_event(event: dict[str, Any]) -> str:
    tool_call_id = event.get("tool_call_id")
    return tool_call_id if isinstance(tool_call_id, str) and tool_call_id else "unknown_tool_call"


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
