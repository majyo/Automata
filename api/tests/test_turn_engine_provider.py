"""M4: the turn engine runs against an injected ModelProvider.

The point of the port is that a turn can be exercised with an in-memory
double: no HTTP, no API key, no network. These tests assert that the engine
actually uses the injected provider (instead of reaching for the concrete
chat-completions adapter) and that the adapter is the default.
"""

from __future__ import annotations

import asyncio
from typing import Any

from automata_api.agent import runtime
from automata_api.agent.adapters.chat_completions import (
    ChatCompletionsProvider,
    assistant_message_for_provider,
    default_model_provider,
    tool_result_for_provider,
)
from automata_api.agent.tools import ToolResult
from automata_api.config import ContextCompressionConfig


class ScriptedAccumulator:
    def __init__(self) -> None:
        self._content: list[str] = []
        self._tool_calls: list[dict[str, Any]] = []

    def add(self, delta: dict[str, Any]) -> None:
        content = delta.get("content")
        if isinstance(content, str):
            self._content.append(content)
        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list):
            self._tool_calls.extend(tool_calls)

    def message(self) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": "".join(self._content)}
        if self._tool_calls:
            message["tool_calls"] = list(self._tool_calls)
        return message


class ScriptedProvider:
    """A ModelProvider that replays a fixed script of deltas per step."""

    def __init__(self, steps: list[list[dict[str, Any]]]) -> None:
        self._steps = list(steps)
        self.calls: list[list[dict[str, Any]]] = []

    def accumulator(self) -> ScriptedAccumulator:
        return ScriptedAccumulator()

    async def _stream(self, messages, *, tools):
        self.calls.append(list(messages))
        step = self._steps.pop(0) if self._steps else []
        for delta in step:
            yield delta

    def stream(self, messages, *, tools):
        return self._stream(messages, tools=tools)


def compression_config() -> ContextCompressionConfig:
    return ContextCompressionConfig(
        enabled=False,
        threshold_chars=10**9,
        target_chars=1000,
    )


def run_loop(
    provider: ScriptedProvider,
    *,
    max_steps: int = 4,
    tool_runner=None,
) -> list[dict[str, Any]]:
    async def scenario() -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        async for event in runtime.stream_model_loop(
            messages=[{"role": "user", "content": "hi"}],
            compression_config=compression_config(),
            model="test-model",
            mode="act",
            allowed_tool_names=None,
            max_steps=max_steps,
            tools=[],
            provider=provider,
            tool_runner=tool_runner,
        ):
            events.append(event)
        return events

    return asyncio.run(scenario())


def test_engine_uses_the_injected_provider():
    provider = ScriptedProvider([[{"content": "hello "}, {"content": "world"}]])

    events = run_loop(provider)

    assert len(provider.calls) == 1
    final = [event for event in events if event["type"] == "final"]
    assert final == [{"type": "final", "content": "hello world", "mode": "act"}]
    tokens = [event["content"] for event in events if event["type"] == "token"]
    assert tokens == ["hello ", "world"]


def test_injected_provider_sees_the_message_history():
    provider = ScriptedProvider([[{"content": "ok"}]])

    run_loop(provider)

    assert provider.calls[0][0] == {"role": "user", "content": "hi"}


def test_default_provider_is_the_chat_completions_adapter():
    assert isinstance(default_model_provider, ChatCompletionsProvider)


def test_engine_reports_empty_provider_response_as_a_provider_error():
    import pytest

    from automata_api.agent.llm import AgentProviderError

    provider = ScriptedProvider([[]])

    with pytest.raises(AgentProviderError):
        run_loop(provider)


def tool_call_step(name: str = "some_tool") -> list[dict[str, Any]]:
    """One model step that requests a tool, keeping the loop going."""
    return [
        {
            "tool_calls": [
                {
                    "id": f"call-{name}",
                    "function": {"name": name, "arguments": "{}"},
                }
            ]
        }
    ]


def test_engine_stops_at_the_step_limit():
    """A turn that never finishes must fail rather than loop forever."""
    import pytest

    from automata_api.agent.llm import AgentProviderError

    provider = ScriptedProvider([tool_call_step() for _ in range(4)])

    with pytest.raises(AgentProviderError, match="maximum step limit"):
        run_loop(provider, max_steps=2)

    assert len(provider.calls) == 2


def test_assistant_message_conversion_keeps_tool_calls_and_reasoning():
    message = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "call-1", "function": {"name": "read_file"}}],
        "reasoning_content": "thinking",
    }

    converted = assistant_message_for_provider(message)

    assert converted["content"] is None
    assert converted["tool_calls"] == message["tool_calls"]
    assert converted["reasoning_content"] == "thinking"


def test_tool_result_conversion_uses_the_call_id():
    result = ToolResult(
        name="read_file", arguments={}, content="payload", success=True
    )

    converted = tool_result_for_provider({"id": "call-9"}, result)

    assert converted == {
        "role": "tool",
        "tool_call_id": "call-9",
        "content": "payload",
    }


def test_engine_uses_the_injected_tool_runner():
    """The builtin-tool fallback is a port, not a hard-coded import."""
    calls: list[tuple[str, object, str]] = []

    async def fake_runner(name, arguments, workspace):
        calls.append((name, arguments, workspace))
        return ToolResult(
            name=name, arguments={}, content='{"ok": true}', success=True
        )

    provider = ScriptedProvider([tool_call_step("read_file"), [{"content": "done"}]])

    events = run_loop(provider, tool_runner=fake_runner)

    assert calls == [("read_file", "{}", "")]
    results = [event for event in events if event["type"] == "tool_result"]
    assert results and results[0]["tool"] == "read_file"
    assert results[0]["success"] is True


def test_default_tool_runner_resolves_the_runtime_seam():
    """The default must resolve late so test doubles keep working."""
    import automata_api.agent.runtime as runtime
    from automata_api.agent.tool_dispatch import DefaultToolRunner

    sentinel_calls: list[str] = []
    original = runtime.run_tool

    async def fake(name, arguments, workspace):
        sentinel_calls.append(name)
        return ToolResult(name=name, arguments={}, content="{}", success=True)

    runtime.run_tool = fake
    try:
        result = asyncio.run(
            DefaultToolRunner()("read_file", "{}", "workspace")
        )
    finally:
        runtime.run_tool = original

    assert sentinel_calls == ["read_file"]
    assert result.success is True
