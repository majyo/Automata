import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from automata_api.agent import llm
from automata_api.agent.context import (
    compress_loop_context_if_needed,
    fetch_agent_context,
)
from automata_api.agent.execution.model import CancellationToken, ToolExecutionContext
from automata_api.agent.execution.orchestrator import ToolExecutionOrchestrator
from automata_api.agent.prompts import (
    agent_system_prompt,
    approved_plan_message,
    plan_system_prompt,
)
from automata_api.agent.skills.model import SkillTurnContext
from automata_api.agent.tools import ToolResult, run_tool, tool_specs
from automata_api.agent.tools.registry import ToolRegistry, registered_tools
from automata_api.agent.tools.router import ToolRouter
from automata_api.agent.tools.thread_context import SEARCH_THREAD_CONTEXT_NAME
from automata_api.agent.types import AgentContextStore, AgentLoopEvent
from automata_api.config import (
    DEFAULT_AGENT_MAX_STEPS,
    ContextCompressionConfig,
    get_agent_config,
    get_context_compression_config,
)
from automata_api.db.context_search import (
    CONTEXT_SOURCE_CONVERSATION,
    CONTEXT_SOURCE_SEARCH,
)
from automata_api.observability import (
    emit_content_record,
    observe_span,
)

PLAN_TOOL_NAMES = {tool.name for tool in registered_tools() if tool.read_only}
MAX_TOOL_OUTPUT_EVENT_CHARS = 8_192
MAX_TOOL_OUTPUT_CHARS_PER_CALL = 262_144


class EventCollector:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)


async def stream_agent_loop(
    *,
    session_id: str,
    store: AgentContextStore,
    workspace: str | None = None,
    workspace_label: str | None = None,
    router: ToolRouter | None = None,
    registry: ToolRegistry | None = None,
    tool_notes: str | None = None,
    skill_context: SkillTurnContext | None = None,
    approved_plan_content: str | None = None,
    run_id: str | None = None,
    cancellation: CancellationToken | None = None,
    orchestrator: ToolExecutionOrchestrator | None = None,
) -> AsyncIterator[AgentLoopEvent]:
    if cancellation is not None:
        cancellation.raise_if_cancelled()
    config = get_agent_config()
    compression_config = get_context_compression_config()
    collector = EventCollector()
    async with observe_span(
        "context.load",
        attributes={"compression_enabled": compression_config.enabled},
    ):
        messages = await fetch_agent_context(
            emit_event=collector.emit,
            session_id=session_id,
            store=store,
            compression_config=compression_config,
            system_prompt=agent_system_prompt(
                workspace_label or workspace,
                tool_notes=tool_notes,
                skill_notes=(
                    skill_context.available_notes if skill_context else None
                ),
            ),
        )
    for event in collector.events:
        yield event

    if approved_plan_content:
        messages.insert(1, approved_plan_message(approved_plan_content))
    insert_skill_messages(
        messages,
        skill_context,
        index=2 if approved_plan_content else 1,
    )
    tools = router.model_visible_specs(mode="act") if router is not None else tool_specs()

    async for event in stream_model_loop(
        messages=messages,
        router=router,
        tools=tools,
        compression_config=compression_config,
        model=config.model,
        max_steps=config.max_steps,
        mode="act",
        allowed_tool_names=None,
        workspace=workspace,
        session_id=session_id,
        store=store,
        run_id=run_id,
        cancellation=cancellation,
        orchestrator=orchestrator,
    ):
        yield event


async def stream_plan_loop(
    *,
    session_id: str,
    store: AgentContextStore,
    workspace: str | None = None,
    workspace_label: str | None = None,
    router: ToolRouter | None = None,
    registry: ToolRegistry | None = None,
    tool_notes: str | None = None,
    skill_context: SkillTurnContext | None = None,
    run_id: str | None = None,
    cancellation: CancellationToken | None = None,
    orchestrator: ToolExecutionOrchestrator | None = None,
) -> AsyncIterator[AgentLoopEvent]:
    if cancellation is not None:
        cancellation.raise_if_cancelled()
    config = get_agent_config()
    compression_config = get_context_compression_config()
    collector = EventCollector()
    allowed_tool_names = (
        router.allowed_names(mode="plan")
        if router is not None
        else (
            registry.allowed_names(read_only_only=True)
            if registry is not None
            else PLAN_TOOL_NAMES
        )
    )
    async with observe_span(
        "context.load",
        attributes={"compression_enabled": compression_config.enabled},
    ):
        messages = await fetch_agent_context(
            emit_event=collector.emit,
            session_id=session_id,
            store=store,
            compression_config=compression_config,
            system_prompt=plan_system_prompt(
                workspace_label or workspace,
                allowed_tool_names=allowed_tool_names,
                tool_notes=tool_notes,
                skill_notes=(
                    skill_context.available_notes if skill_context else None
                ),
            ),
        )
    for event in collector.events:
        yield event
    insert_skill_messages(messages, skill_context, index=1)
    tools = (
        router.model_visible_specs(mode="plan")
        if router is not None
        else (
            registry.specs(read_only_only=True)
            if registry is not None
            else tool_specs_for_names(tool_specs(), PLAN_TOOL_NAMES)
        )
    )

    async for event in stream_model_loop(
        messages=messages,
        router=router,
        tools=tools,
        compression_config=compression_config,
        model=config.model,
        max_steps=config.max_steps,
        mode="plan",
        allowed_tool_names=allowed_tool_names,
        workspace=workspace,
        session_id=session_id,
        store=store,
        run_id=run_id,
        cancellation=cancellation,
        orchestrator=orchestrator,
    ):
        yield event


async def stream_model_loop(
    *,
    messages: list[dict[str, Any]],
    compression_config: ContextCompressionConfig,
    model: str,
    mode: str,
    allowed_tool_names: set[str] | None,
    max_steps: int = DEFAULT_AGENT_MAX_STEPS,
    router: ToolRouter | None = None,
    tools: list[dict[str, Any]] | None = None,
    workspace: str | None = None,
    registry: ToolRegistry | None = None,
    session_id: str | None = None,
    store: AgentContextStore | None = None,
    run_id: str | None = None,
    cancellation: CancellationToken | None = None,
    orchestrator: ToolExecutionOrchestrator | None = None,
) -> AsyncIterator[AgentLoopEvent]:
    for step in range(1, max_steps + 1):
        async with observe_span(
            "agent.step",
            attributes={
                "step": step,
                "mode": mode,
                "model": model,
                "message_count": len(messages),
            },
        ) as step_span:
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            yield {
                "type": "agent_step",
                "step": step,
                "mode": mode,
                "message": f"Calling model {model}",
            }
            async with observe_span("tools.specs.build"):
                current_tools = (
                    router.model_visible_specs(mode=mode)
                    if router is not None
                    else (tools or [])
                )
            step_span.set_attributes(tool_spec_count=len(current_tools))
            accumulator = llm.AssistantStreamAccumulator()
            tool_call_started = False
            emitted_text = False
            async for delta in llm.stream_chat_completion(
                messages, tools=current_tools
            ):
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                accumulator.add(delta)
                if delta.get("tool_calls"):
                    tool_call_started = True

                content = delta.get("content")
                if isinstance(content, str) and content and (
                    emitted_text or not tool_call_started
                ):
                    emitted_text = True
                    yield {"type": "token", "content": content}

            assistant_message = accumulator.message()
            emit_content_record(
                "llm.assistant_message", assistant_message
            )
            tool_calls = assistant_message.get("tool_calls")

            if isinstance(tool_calls, list) and tool_calls:
                step_span.set_attributes(
                    outcome="tool_calls",
                    tool_call_count=len(tool_calls),
                )
                provider_message = assistant_message_for_provider(
                    assistant_message
                )
                messages.append(provider_message)
                async with observe_span("context.message.persist"):
                    await save_context_message_if_possible(
                        store=store,
                        session_id=session_id,
                        message=provider_message,
                        source=context_source_for_assistant_message(
                            provider_message
                        ),
                    )
                for tool_call in tool_calls:
                    async for event in stream_execute_tool_call(
                        messages=messages,
                        tool_call=tool_call,
                        mode=mode,
                        allowed_tool_names=allowed_tool_names,
                        workspace=workspace,
                        router=router,
                        registry=registry,
                        session_id=session_id,
                        store=store,
                        run_id=run_id,
                        cancellation=cancellation,
                        orchestrator=orchestrator,
                    ):
                        yield event
                collector = EventCollector()
                async with observe_span("context.compress"):
                    messages = await compress_loop_context_if_needed(
                        emit_event=collector.emit,
                        messages=messages,
                        compression_config=compression_config,
                    )
                for event in collector.events:
                    yield event
                continue

            content = assistant_message.get("content")
            if isinstance(content, str) and content.strip():
                step_span.set_attributes(
                    outcome="final",
                    content_chars=len(content),
                )
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                async with observe_span("response.persist"):
                    await save_context_message_if_possible(
                        store=store,
                        session_id=session_id,
                        message={"role": "assistant", "content": content},
                    )
                yield {"type": "final", "content": content, "mode": mode}
                return

            step_span.set_status(
                "error", error_type="empty_model_response"
            )
            raise llm.AgentProviderError(
                "LLM provider returned an empty response."
            )

    raise llm.AgentProviderError(
        f"Agent reached the maximum step limit ({max_steps}) before finishing."
    )


async def stream_execute_tool_call(
    *,
    messages: list[dict[str, Any]],
    tool_call: dict[str, Any],
    workspace: str | None = None,
    router: ToolRouter | None = None,
    registry: ToolRegistry | None = None,
    mode: str = "act",
    allowed_tool_names: set[str] | None = None,
    session_id: str | None = None,
    store: AgentContextStore | None = None,
    run_id: str | None = None,
    cancellation: CancellationToken | None = None,
    orchestrator: ToolExecutionOrchestrator | None = None,
) -> AsyncIterator[AgentLoopEvent]:
    function = tool_call.get("function")
    function = function if isinstance(function, dict) else {}
    name = function.get("name")
    name = name if isinstance(name, str) and name else "unknown_tool"
    raw_arguments = function.get("arguments")
    raw_arguments = (
        raw_arguments if isinstance(raw_arguments, str) else "{}"
    )
    call_id = tool_call.get("id")
    call_id = call_id if isinstance(call_id, str) else ""
    async with observe_span(
        "tool.call",
        attributes={
            "tool": name,
            "tool_call_id": call_id,
            "mode": mode,
            "argument_chars": len(raw_arguments),
        },
        critical=True,
    ) as tool_span:
        emit_content_record(
            "tool.request",
            {
                "tool": name,
                "tool_call_id": call_id,
                "arguments": raw_arguments,
            },
        )
        async for event in _stream_execute_tool_call_inner(
            messages=messages,
            tool_call=tool_call,
            workspace=workspace,
            router=router,
            registry=registry,
            mode=mode,
            allowed_tool_names=allowed_tool_names,
            session_id=session_id,
            store=store,
            run_id=run_id,
            cancellation=cancellation,
            orchestrator=orchestrator,
        ):
            if event.get("type") == "tool_result":
                content = event.get("content")
                content = content if isinstance(content, str) else ""
                success = event.get("success") is not False
                tool_span.set_attributes(
                    success=success,
                    result_chars=len(content),
                )
                if not success:
                    tool_span.set_status(
                        "error", error_type="tool_result_failed"
                    )
                emit_content_record("tool.response", event)
            yield event


async def _stream_execute_tool_call_inner(
    *,
    messages: list[dict[str, Any]],
    tool_call: dict[str, Any],
    workspace: str | None = None,
    router: ToolRouter | None = None,
    registry: ToolRegistry | None = None,
    mode: str = "act",
    allowed_tool_names: set[str] | None = None,
    session_id: str | None = None,
    store: AgentContextStore | None = None,
    run_id: str | None = None,
    cancellation: CancellationToken | None = None,
    orchestrator: ToolExecutionOrchestrator | None = None,
) -> AsyncIterator[AgentLoopEvent]:
    if cancellation is not None:
        cancellation.raise_if_cancelled()
    function = tool_call.get("function")
    if not isinstance(function, dict):
        raise llm.AgentProviderError("LLM provider returned an invalid tool call.")

    name = function.get("name")
    arguments = function.get("arguments")
    call_id = tool_call.get("id")
    if not isinstance(name, str) or not name.strip():
        raise llm.AgentProviderError("LLM provider returned a tool call without a name.")
    if not isinstance(call_id, str) or not call_id.strip():
        call_id = ""

    yield {
        "type": "tool_call",
        "tool_call_id": call_id,
        "tool": name,
        "arguments": arguments if isinstance(arguments, str) else "{}",
    }
    if (
        router is not None
        and orchestrator is not None
        and run_id is not None
        and session_id is not None
        and cancellation is not None
    ):
        output_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        output_chars = 0

        async def emit_output(event: dict[str, Any]) -> None:
            nonlocal output_chars
            content = event.get("content")
            if not isinstance(content, str) or not content:
                return
            remaining = MAX_TOOL_OUTPUT_CHARS_PER_CALL - output_chars
            if remaining <= 0:
                return
            bounded = content[: min(remaining, MAX_TOOL_OUTPUT_EVENT_CHARS)]
            output_chars += len(bounded)
            await output_events.put(
                {
                    **event,
                    "content": bounded,
                    "truncated": len(bounded) < len(content),
                }
            )

        execution_task = asyncio.create_task(
            orchestrator.execute(
                router=router,
                tool_name=name,
                raw_arguments=arguments,
                context=ToolExecutionContext(
                    run_id=run_id,
                    session_id=session_id,
                    tool_call_id=call_id,
                    workspace=workspace or "",
                    mode="plan" if mode == "plan" else "act",
                    cancellation=cancellation,
                    emit_event=emit_output,
                ),
            )
        )
        try:
            while not execution_task.done():
                try:
                    output_event = await asyncio.wait_for(
                        output_events.get(),
                        timeout=0.05,
                    )
                except TimeoutError:
                    continue
                yield output_event
            while not output_events.empty():
                yield output_events.get_nowait()
            result = await execution_task
        except BaseException:
            if not execution_task.done():
                execution_task.cancel()
            await asyncio.gather(execution_task, return_exceptions=True)
            raise
    elif router is not None:
        result = await router.dispatch(name, arguments, mode=mode)
    elif allowed_tool_names is not None and name not in allowed_tool_names:
        result = blocked_tool_result(name, arguments, mode, allowed_tool_names)
    elif registry is not None:
        result = await registry.run(name, arguments)
    else:
        result = await run_tool(name, arguments, workspace or "")
    if cancellation is not None:
        cancellation.raise_if_cancelled()
    yield {
        "type": "tool_result",
        "tool_call_id": call_id,
        "tool": result.name,
        "success": result.success,
        "content": result.content,
    }
    provider_message = tool_result_for_provider(tool_call, result)
    messages.append(provider_message)
    await save_context_message_if_possible(
        store=store,
        session_id=session_id,
        message=provider_message,
        source=(
            CONTEXT_SOURCE_SEARCH
            if result.name == SEARCH_THREAD_CONTEXT_NAME
            else CONTEXT_SOURCE_CONVERSATION
        ),
    )


async def save_context_message_if_possible(
    *,
    store: AgentContextStore | None,
    session_id: str | None,
    message: dict[str, Any],
    source: str = CONTEXT_SOURCE_CONVERSATION,
) -> None:
    if store is None or not session_id:
        return

    if source == CONTEXT_SOURCE_CONVERSATION:
        await asyncio.to_thread(store.save_context_message, session_id, message)
        return

    await asyncio.to_thread(
        store.save_context_message,
        session_id,
        message,
        source=source,
    )


def context_source_for_assistant_message(message: dict[str, Any]) -> str:
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list) or not tool_calls:
        return CONTEXT_SOURCE_CONVERSATION

    names: list[str] = []
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            return CONTEXT_SOURCE_CONVERSATION
        function = tool_call.get("function")
        if not isinstance(function, dict):
            return CONTEXT_SOURCE_CONVERSATION
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            return CONTEXT_SOURCE_CONVERSATION
        names.append(name)

    if names and all(name == SEARCH_THREAD_CONTEXT_NAME for name in names):
        return CONTEXT_SOURCE_SEARCH
    return CONTEXT_SOURCE_CONVERSATION


def insert_skill_messages(
    messages: list[dict[str, Any]],
    skill_context: SkillTurnContext | None,
    *,
    index: int,
) -> None:
    if skill_context is None or not skill_context.injected_messages:
        return

    bounded_index = max(1, min(index, len(messages)))
    messages[bounded_index:bounded_index] = [
        dict(message) for message in skill_context.injected_messages
    ]


def tool_specs_for_names(
    tools: list[dict[str, Any]], allowed_names: set[str]
) -> list[dict[str, Any]]:
    return [
        tool
        for tool in tools
        if tool_name(tool) is not None and tool_name(tool) in allowed_names
    ]


def tool_name(tool: dict[str, Any]) -> str | None:
    function = tool.get("function")
    if not isinstance(function, dict):
        return None

    name = function.get("name")
    return name if isinstance(name, str) and name else None


def blocked_tool_result(
    name: str,
    raw_arguments: str | dict[str, Any] | None,
    mode: str,
    allowed_tool_names: set[str],
) -> ToolResult:
    arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
    return ToolResult(
        name=name,
        arguments=arguments,
        content=json.dumps(
            {
                "simulated": False,
                "ok": False,
                "tool": name,
                "mode": mode,
                "error": "blocked_by_plan_mode",
                "allowed_tools": sorted(allowed_tool_names),
            },
            ensure_ascii=True,
        ),
        success=False,
    )


def assistant_message_for_provider(message: dict[str, Any]) -> dict[str, Any]:
    content = message.get("content")
    provider_message: dict[str, Any] = {
        "role": "assistant",
        "content": content if isinstance(content, str) and content else None,
    }

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and tool_calls:
        provider_message["tool_calls"] = tool_calls

    reasoning_content = message.get("reasoning_content")
    if isinstance(reasoning_content, str):
        provider_message["reasoning_content"] = reasoning_content

    return provider_message


def tool_result_for_provider(
    tool_call: dict[str, Any], result: ToolResult
) -> dict[str, Any]:
    call_id = tool_call.get("id")
    return {
        "role": "tool",
        "tool_call_id": call_id if isinstance(call_id, str) else "",
        "content": result.content,
    }
