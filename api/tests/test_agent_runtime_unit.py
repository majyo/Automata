import asyncio
import json
from dataclasses import dataclass

import pytest

from automata_api.agent import llm, runtime
from automata_api.agent.tools import ToolResult
from automata_api.config import AgentConfig, ContextCompressionConfig


@dataclass
class MemoryStore:
    recent_messages: list[dict]

    def get_recent_messages(self, session_id: str, limit: int) -> list[dict]:
        return self.recent_messages[-limit:]

    def get_messages_after_sequence(self, session_id: str, sequence: int) -> list[dict]:
        return []

    def fetch_context_summary(self, session_id: str) -> dict | None:
        return None

    def upsert_context_summary(
        self, session_id: str, content: str, through_sequence: int
    ) -> dict:
        return {
            "session_id": session_id,
            "content": content,
            "through_sequence": through_sequence,
        }


class EventRecorder:
    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)


def configure_runtime(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "get_agent_config",
        lambda: AgentConfig(
            api_key="test-key",
            base_url="https://provider.test",
            model="unit-model",
            timeout_seconds=30.0,
            temperature=0.2,
        ),
    )
    monkeypatch.setattr(
        runtime,
        "get_context_compression_config",
        lambda: ContextCompressionConfig(
            enabled=False,
            threshold_chars=10_000,
            target_chars=1_000,
        ),
    )


def test_run_agent_loop_injects_approved_plan(monkeypatch):
    configure_runtime(monkeypatch)
    calls = []

    async def fake_create_llm_response(messages, tools=None):
        calls.append({"messages": messages, "tools": tools})
        return {"role": "assistant", "content": "done", "tool_calls": []}

    monkeypatch.setattr(llm, "create_llm_response", fake_create_llm_response)

    recorder = EventRecorder()
    response = asyncio.run(
        runtime.run_agent_loop(
            session_id="session-1",
            store=MemoryStore(
                recent_messages=[{"role": "user", "content": "implement it"}]
            ),
            emit_event=recorder.emit,
            approved_plan_content="Approved plan body",
        )
    )

    assert response == "done"
    assert calls[0]["messages"][0]["role"] == "system"
    assert "Approved plan body" in calls[0]["messages"][1]["content"]
    assert calls[0]["messages"][2] == {"role": "user", "content": "implement it"}
    assert {tool["function"]["name"] for tool in calls[0]["tools"]} >= {
        "run_bash",
        "write_file",
        "apply_patch",
    }
    assert recorder.events == [
        {
            "type": "agent_step",
            "step": 1,
            "mode": "act",
            "message": "Calling model unit-model",
        }
    ]


def test_run_plan_loop_exposes_only_plan_mode_tools(monkeypatch):
    configure_runtime(monkeypatch)
    calls = []

    async def fake_create_llm_response(messages, tools=None):
        calls.append({"messages": messages, "tools": tools})
        return {"role": "assistant", "content": "plan", "tool_calls": []}

    monkeypatch.setattr(llm, "create_llm_response", fake_create_llm_response)

    recorder = EventRecorder()
    response = asyncio.run(
        runtime.run_plan_loop(
            session_id="session-1",
            store=MemoryStore(recent_messages=[]),
            emit_event=recorder.emit,
        )
    )

    tool_names = {tool["function"]["name"] for tool in calls[0]["tools"]}
    assert response == "plan"
    assert tool_names == runtime.PLAN_TOOL_NAMES
    assert {"run_bash", "write_file", "apply_patch"}.isdisjoint(tool_names)
    assert "backend Plan mode" in calls[0]["messages"][0]["content"]
    assert recorder.events[0]["mode"] == "plan"


def test_run_model_loop_returns_content_without_tools(monkeypatch):
    async def fake_create_llm_response(messages, tools=None):
        assert messages == [{"role": "user", "content": "hello"}]
        assert tools == []
        return {"role": "assistant", "content": "final answer", "tool_calls": []}

    monkeypatch.setattr(llm, "create_llm_response", fake_create_llm_response)

    recorder = EventRecorder()
    response = asyncio.run(
        runtime.run_model_loop(
            emit_event=recorder.emit,
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
            compression_config=ContextCompressionConfig(False, 1_000, 100),
            model="unit-model",
            mode="act",
            allowed_tool_names=None,
        )
    )

    assert response == "final answer"
    assert [event["type"] for event in recorder.events] == ["agent_step"]


def test_run_model_loop_executes_tool_then_continues(monkeypatch):
    calls = []

    async def fake_create_llm_response(messages, tools=None):
        calls.append(list(messages))
        if len(calls) == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            }

        assert messages[-1]["role"] == "tool"
        assert messages[-1]["name"] == "read_file"
        return {"role": "assistant", "content": "after tool", "tool_calls": []}

    async def fake_run_tool(name, arguments, workspace):
        return ToolResult(
            name=name,
            arguments={},
            content='{"ok": true}',
            success=True,
        )

    monkeypatch.setattr(llm, "create_llm_response", fake_create_llm_response)
    monkeypatch.setattr(runtime, "run_tool", fake_run_tool)
    monkeypatch.setattr(runtime, "agent_workspace", lambda: "workspace")

    recorder = EventRecorder()
    response = asyncio.run(
        runtime.run_model_loop(
            emit_event=recorder.emit,
            messages=[{"role": "user", "content": "inspect"}],
            tools=[],
            compression_config=ContextCompressionConfig(False, 1_000, 100),
            model="unit-model",
            mode="act",
            allowed_tool_names=None,
        )
    )

    assert response == "after tool"
    assert [event["type"] for event in recorder.events] == [
        "agent_step",
        "tool_call",
        "tool_result",
        "agent_step",
    ]
    assert len(calls) == 2


def test_run_model_loop_rejects_empty_response(monkeypatch):
    async def fake_create_llm_response(messages, tools=None):
        return {"role": "assistant", "content": "  ", "tool_calls": []}

    monkeypatch.setattr(llm, "create_llm_response", fake_create_llm_response)

    with pytest.raises(llm.AgentProviderError, match="empty response"):
        asyncio.run(
            runtime.run_model_loop(
                emit_event=EventRecorder().emit,
                messages=[],
                tools=[],
                compression_config=ContextCompressionConfig(False, 1_000, 100),
                model="unit-model",
                mode="act",
                allowed_tool_names=None,
            )
        )


def test_run_model_loop_rejects_max_steps(monkeypatch):
    async def fake_create_llm_response(messages, tools=None):
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_repeat",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        }

    async def fake_run_tool(name, arguments, workspace):
        return ToolResult(name=name, arguments={}, content="{}", success=True)

    monkeypatch.setattr(llm, "create_llm_response", fake_create_llm_response)
    monkeypatch.setattr(runtime, "run_tool", fake_run_tool)
    monkeypatch.setattr(runtime, "agent_workspace", lambda: "workspace")

    with pytest.raises(llm.AgentProviderError, match="maximum step limit"):
        asyncio.run(
            runtime.run_model_loop(
                emit_event=EventRecorder().emit,
                messages=[],
                tools=[],
                compression_config=ContextCompressionConfig(False, 1_000, 100),
                model="unit-model",
                mode="act",
                allowed_tool_names=None,
            )
        )


def test_execute_tool_call_emits_tool_result(monkeypatch):
    async def fake_run_tool(name, arguments, workspace):
        assert name == "read_file"
        assert arguments == '{"path": "README.md"}'
        assert workspace == "workspace"
        return ToolResult(name=name, arguments={}, content='{"content": "ok"}', success=True)

    monkeypatch.setattr(runtime, "run_tool", fake_run_tool)
    monkeypatch.setattr(runtime, "agent_workspace", lambda: "workspace")

    messages = []
    recorder = EventRecorder()
    asyncio.run(
        runtime.execute_tool_call(
            emit_event=recorder.emit,
            messages=messages,
            tool_call={
                "id": "call_read",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path": "README.md"}',
                },
            },
        )
    )

    assert recorder.events[0] == {
        "type": "tool_call",
        "tool": "read_file",
        "arguments": '{"path": "README.md"}',
    }
    assert recorder.events[1]["success"] is True
    assert messages == [
        {
            "role": "tool",
            "tool_call_id": "call_read",
            "name": "read_file",
            "content": '{"content": "ok"}',
        }
    ]


def test_execute_tool_call_blocks_disallowed_plan_tool(monkeypatch):
    async def fail_run_tool(name, arguments, workspace):
        raise AssertionError("blocked tools must not execute")

    monkeypatch.setattr(runtime, "run_tool", fail_run_tool)

    recorder = EventRecorder()
    messages = []
    asyncio.run(
        runtime.execute_tool_call(
            emit_event=recorder.emit,
            messages=messages,
            tool_call={
                "id": "call_write",
                "type": "function",
                "function": {"name": "write_file", "arguments": '{"path": "x"}'},
            },
            mode="plan",
            allowed_tool_names={"read_file"},
        )
    )

    result = json.loads(messages[-1]["content"])
    assert recorder.events[1]["success"] is False
    assert result["error"] == "blocked_by_plan_mode"
    assert result["allowed_tools"] == ["read_file"]


@pytest.mark.parametrize(
    "tool_call, message",
    [
        ({}, "invalid tool call"),
        ({"function": {"arguments": "{}"}}, "without a name"),
    ],
)
def test_execute_tool_call_rejects_invalid_tool_call(tool_call, message):
    with pytest.raises(llm.AgentProviderError, match=message):
        asyncio.run(
            runtime.execute_tool_call(
                emit_event=EventRecorder().emit,
                messages=[],
                tool_call=tool_call,
            )
        )


def test_runtime_conversion_helpers():
    assistant = runtime.assistant_message_for_provider(
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "thinking",
            "tool_calls": [{"id": "call_1"}],
        }
    )
    tool_result = runtime.tool_result_for_provider(
        {"id": "call_1"},
        ToolResult("read_file", {"path": "README.md"}, "ok", True),
    )
    blocked = runtime.blocked_tool_result(
        "write_file",
        {"path": "x"},
        "plan",
        {"read_file", "rg"},
    )
    filtered = runtime.tool_specs_for_names(
        [
            {"function": {"name": "read_file"}},
            {"function": {"name": "write_file"}},
            {"not_function": True},
        ],
        {"read_file"},
    )

    assert assistant == {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "call_1"}],
        "reasoning_content": "thinking",
    }
    assert tool_result == {
        "role": "tool",
        "tool_call_id": "call_1",
        "name": "read_file",
        "content": "ok",
    }
    assert json.loads(blocked.content)["allowed_tools"] == ["read_file", "rg"]
    assert filtered == [{"function": {"name": "read_file"}}]
    assert runtime.tool_name({"function": {"name": ""}}) is None
    assert runtime.tool_name({"function": "not-a-dict"}) is None
