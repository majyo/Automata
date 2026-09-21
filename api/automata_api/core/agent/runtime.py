import asyncio
from collections.abc import AsyncIterator
from typing import Any

from automata_api.config import (
    DEFAULT_AGENT_MAX_STEPS,
    ContextCompressionConfig,
)
from automata_api.core.agent import messages as llm
from automata_api.core.agent.context import (
    compress_loop_context_if_needed,
    fetch_agent_context,
)
from automata_api.core.agent.messages import (
    assistant_message_for_provider,
    tool_result_for_provider,
)
from automata_api.core.agent.ports import AgentInputChannel, ModelProvider
from automata_api.core.agent.prompts import (
    agent_system_prompt,
    approved_plan_message,
    plan_system_prompt,
)
from automata_api.core.agent.settings import TurnSettings
from automata_api.core.agent.skills.model import SkillTurnContext
from automata_api.core.agent.tool_dispatch import RunTool
from automata_api.core.agent.turn import EventCollector as EventCollector
from automata_api.core.agent.turn import blocked_tool_result as blocked_tool_result
from automata_api.core.agent.turn import insert_skill_messages as insert_skill_messages
from automata_api.core.agent.turn import tool_name as tool_name
from automata_api.core.agent.turn import tool_specs_for_names as tool_specs_for_names
from automata_api.core.agent.types import AgentLoopEvent
from automata_api.core.runs.model import CancellationToken, ToolExecutionContext
from automata_api.core.sessions.context_sources import (
    CONTEXT_SOURCE_CONVERSATION,
    source_for_assistant_message,
    source_for_tool_result,
)
from automata_api.core.sessions.ports import ContextStore
from automata_api.core.telemetry import emit_content_record, observe_span
from automata_api.core.tools.orchestrator import ToolExecutionOrchestrator
from automata_api.core.tools.registry import ToolRegistry, registered_tools, tool_specs
from automata_api.core.tools.router import ToolRouter
from automata_api.core.tools.thread_context import SEARCH_THREAD_CONTEXT_NAME

PLAN_TOOL_NAMES = {tool.name for tool in registered_tools() if tool.read_only}
MAX_TOOL_OUTPUT_EVENT_CHARS = 8_192
MAX_TOOL_OUTPUT_CHARS_PER_CALL = 262_144


async def stream_agent_loop(
    *,
    provider: ModelProvider,
    settings: TurnSettings | None = None,
    session_id: str,
    store: ContextStore,
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
    tool_runner: RunTool | None = None,
    input_channel: AgentInputChannel | None = None,
) -> AsyncIterator[AgentLoopEvent]:
    if cancellation is not None:
        cancellation.raise_if_cancelled()
    config = settings or TurnSettings()
    compression_config = config.compression
    collector = EventCollector()
    async with observe_span(
        "context.load",
        attributes={"compression_enabled": compression_config.enabled},
    ):
        messages = await fetch_agent_context(
            provider=provider,
            emit_event=collector.emit,
            session_id=session_id,
            store=store,
            compression_config=compression_config,
            system_prompt=agent_system_prompt(
                workspace_label or workspace,
                tool_notes=tool_notes,
                skill_notes=(skill_context.available_notes if skill_context else None),
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
    tools = (
        router.model_visible_specs(mode="act") if router is not None else tool_specs()
    )

    async for event in stream_model_loop(
        provider=provider,
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
        tool_runner=tool_runner,
        input_channel=input_channel,
    ):
        yield event


async def stream_plan_loop(
    *,
    provider: ModelProvider,
    settings: TurnSettings | None = None,
    session_id: str,
    store: ContextStore,
    workspace: str | None = None,
    workspace_label: str | None = None,
    router: ToolRouter | None = None,
    registry: ToolRegistry | None = None,
    tool_notes: str | None = None,
    skill_context: SkillTurnContext | None = None,
    run_id: str | None = None,
    cancellation: CancellationToken | None = None,
    orchestrator: ToolExecutionOrchestrator | None = None,
    tool_runner: RunTool | None = None,
    input_channel: AgentInputChannel | None = None,
) -> AsyncIterator[AgentLoopEvent]:
    if cancellation is not None:
        cancellation.raise_if_cancelled()
    config = settings or TurnSettings()
    compression_config = config.compression
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
            provider=provider,
            emit_event=collector.emit,
            session_id=session_id,
            store=store,
            compression_config=compression_config,
            system_prompt=plan_system_prompt(
                workspace_label or workspace,
                allowed_tool_names=allowed_tool_names,
                tool_notes=tool_notes,
                skill_notes=(skill_context.available_notes if skill_context else None),
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
        provider=provider,
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
        tool_runner=tool_runner,
        input_channel=input_channel,
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
    store: ContextStore | None = None,
    run_id: str | None = None,
    cancellation: CancellationToken | None = None,
    orchestrator: ToolExecutionOrchestrator | None = None,
    provider: ModelProvider,
    tool_runner: RunTool | None = None,
    input_channel: AgentInputChannel | None = None,
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
            if input_channel is not None:
                for input_item in await input_channel.take_steering():
                    applied = await input_channel.apply_steering(input_item)
                    messages.append({"role": "user", "content": applied.prompt})
                    yield {
                        "type": "input_applied",
                        "input_id": applied.input_id,
                        "message_id": applied.message_id,
                        "delivery": applied.delivery,
                        "prompt": applied.prompt,
                        "step": step,
                    }
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
            accumulator = provider.accumulator()
            tool_call_started = False
            emitted_text = False
            async for delta in provider.stream(messages, tools=current_tools):
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                accumulator.add(delta)
                if delta.get("tool_calls"):
                    tool_call_started = True

                content = delta.get("content")
                if (
                    isinstance(content, str)
                    and content
                    and (emitted_text or not tool_call_started)
                ):
                    emitted_text = True
                    yield {"type": "token", "content": content}

            assistant_message = accumulator.message()
            emit_content_record("llm.assistant_message", assistant_message)
            tool_calls = assistant_message.get("tool_calls")

            if isinstance(tool_calls, list) and tool_calls:
                step_span.set_attributes(
                    outcome="tool_calls",
                    tool_call_count=len(tool_calls),
                )
                provider_message = assistant_message_for_provider(assistant_message)
                messages.append(provider_message)
                async with observe_span("context.message.persist"):
                    await save_context_message_if_possible(
                        store=store,
                        session_id=session_id,
                        message=provider_message,
                        source=context_source_for_assistant_message(provider_message),
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
                        tool_runner=tool_runner,
                    ):
                        yield event
                collector = EventCollector()
                async with observe_span("context.compress"):
                    messages = await compress_loop_context_if_needed(
                        provider=provider,
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
                if input_channel is not None:
                    steering = await input_channel.seal_and_take_steering()
                    if steering:
                        # The outer turn forwarder flushes the preceding
                        # streamed assistant segment before the user input is
                        # made visible, preserving conversation order.
                        yield {"type": "agent_segment_boundary"}
                        messages.append({"role": "assistant", "content": content})
                        for input_item in steering:
                            applied = await input_channel.apply_steering(input_item)
                            messages.append(
                                {"role": "user", "content": applied.prompt}
                            )
                            yield {
                                "type": "input_applied",
                                "input_id": applied.input_id,
                                "message_id": applied.message_id,
                                "delivery": applied.delivery,
                                "prompt": applied.prompt,
                                "step": step,
                            }
                        continue
                yield {"type": "final", "content": content, "mode": mode}
                return

            step_span.set_status("error", error_type="empty_model_response")
            raise llm.AgentProviderError("LLM provider returned an empty response.")

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
    store: ContextStore | None = None,
    run_id: str | None = None,
    cancellation: CancellationToken | None = None,
    orchestrator: ToolExecutionOrchestrator | None = None,
    tool_runner: RunTool | None = None,
) -> AsyncIterator[AgentLoopEvent]:
    function = tool_call.get("function")
    function = function if isinstance(function, dict) else {}
    name = function.get("name")
    name = name if isinstance(name, str) and name else "unknown_tool"
    raw_arguments = function.get("arguments")
    raw_arguments = raw_arguments if isinstance(raw_arguments, str) else "{}"
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
            tool_runner=tool_runner,
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
                    tool_span.set_status("error", error_type="tool_result_failed")
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
    store: ContextStore | None = None,
    run_id: str | None = None,
    cancellation: CancellationToken | None = None,
    orchestrator: ToolExecutionOrchestrator | None = None,
    tool_runner: RunTool | None = None,
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
        raise llm.AgentProviderError(
            "LLM provider returned a tool call without a name."
        )
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
        if tool_runner is None:
            raise RuntimeError(
                "A ToolExecutor is required when no router or registry is supplied."
            )
        result = await tool_runner(name, arguments, workspace or "")
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
        source=source_for_tool_result(
            result.name, search_tool_name=SEARCH_THREAD_CONTEXT_NAME
        ),
    )


async def save_context_message_if_possible(
    *,
    store: ContextStore | None,
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
    return source_for_assistant_message(
        message, search_tool_name=SEARCH_THREAD_CONTEXT_NAME
    )
