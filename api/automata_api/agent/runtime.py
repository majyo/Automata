import json
from typing import Any

from automata_api.agent import llm
from automata_api.agent.context import (
    compress_loop_context_if_needed,
    fetch_agent_context,
)
from automata_api.agent.prompts import (
    agent_workspace,
    approved_plan_message,
    plan_system_prompt,
)
from automata_api.agent.tools import ToolResult, run_tool, tool_specs
from automata_api.agent.types import AgentContextStore, EventEmitter
from automata_api.config import (
    ContextCompressionConfig,
    get_agent_config,
    get_context_compression_config,
)


MAX_AGENT_STEPS = 6
PLAN_TOOL_NAMES = {
    "read_file",
    "rg",
    "grep",
    "apply_patch_preview",
}


async def run_agent_loop(
    *,
    session_id: str,
    store: AgentContextStore,
    emit_event: EventEmitter,
    approved_plan_content: str | None = None,
) -> str:
    config = get_agent_config()
    compression_config = get_context_compression_config()
    messages = await fetch_agent_context(
        emit_event=emit_event,
        session_id=session_id,
        store=store,
        compression_config=compression_config,
    )
    if approved_plan_content:
        messages.insert(1, approved_plan_message(approved_plan_content))
    tools = tool_specs()

    return await run_model_loop(
        emit_event=emit_event,
        messages=messages,
        tools=tools,
        compression_config=compression_config,
        model=config.model,
        mode="act",
        allowed_tool_names=None,
    )


async def run_plan_loop(
    *,
    session_id: str,
    store: AgentContextStore,
    emit_event: EventEmitter,
) -> str:
    config = get_agent_config()
    compression_config = get_context_compression_config()
    messages = await fetch_agent_context(
        emit_event=emit_event,
        session_id=session_id,
        store=store,
        compression_config=compression_config,
        system_prompt=plan_system_prompt(),
    )
    tools = tool_specs_for_names(tool_specs(), PLAN_TOOL_NAMES)

    return await run_model_loop(
        emit_event=emit_event,
        messages=messages,
        tools=tools,
        compression_config=compression_config,
        model=config.model,
        mode="plan",
        allowed_tool_names=PLAN_TOOL_NAMES,
    )


async def run_model_loop(
    *,
    emit_event: EventEmitter,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    compression_config: ContextCompressionConfig,
    model: str,
    mode: str,
    allowed_tool_names: set[str] | None,
) -> str:
    for step in range(1, MAX_AGENT_STEPS + 1):
        await emit_event(
            {
                "type": "agent_step",
                "step": step,
                "mode": mode,
                "message": f"Calling model {model}",
            }
        )
        assistant_message = await llm.create_llm_response(messages, tools=tools)
        tool_calls = assistant_message.get("tool_calls")

        if isinstance(tool_calls, list) and tool_calls:
            messages.append(assistant_message_for_provider(assistant_message))
            for tool_call in tool_calls:
                await execute_tool_call(
                    emit_event=emit_event,
                    messages=messages,
                    tool_call=tool_call,
                    mode=mode,
                    allowed_tool_names=allowed_tool_names,
                )
            messages = await compress_loop_context_if_needed(
                emit_event=emit_event,
                messages=messages,
                compression_config=compression_config,
            )
            continue

        content = assistant_message.get("content")
        if isinstance(content, str) and content.strip():
            return content

        raise llm.AgentProviderError("LLM provider returned an empty response.")

    raise llm.AgentProviderError(
        f"Agent reached the maximum step limit ({MAX_AGENT_STEPS}) before finishing."
    )


async def execute_tool_call(
    *,
    emit_event: EventEmitter,
    messages: list[dict[str, Any]],
    tool_call: dict[str, Any],
    mode: str = "act",
    allowed_tool_names: set[str] | None = None,
) -> None:
    function = tool_call.get("function")
    if not isinstance(function, dict):
        raise llm.AgentProviderError("LLM provider returned an invalid tool call.")

    name = function.get("name")
    arguments = function.get("arguments")
    if not isinstance(name, str) or not name.strip():
        raise llm.AgentProviderError("LLM provider returned a tool call without a name.")

    await emit_event(
        {
            "type": "tool_call",
            "tool": name,
            "arguments": arguments if isinstance(arguments, str) else "{}",
        }
    )
    if allowed_tool_names is not None and name not in allowed_tool_names:
        result = blocked_tool_result(name, arguments, mode, allowed_tool_names)
    else:
        result = await run_tool(name, arguments, agent_workspace())
    await emit_event(
        {
            "type": "tool_result",
            "tool": result.name,
            "success": result.success,
            "content": result.content,
        }
    )
    messages.append(tool_result_for_provider(tool_call, result))


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
        "name": result.name,
        "content": result.content,
    }
