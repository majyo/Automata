import asyncio
import json
from dataclasses import dataclass

import pytest

from automata_api.agent import llm, runtime
from automata_api.agent.tools import ToolResult
from automata_api.agent.tools.base import AgentTool
from automata_api.agent.tools.model import ToolExposure
from automata_api.agent.tools.providers import descriptor_for_tool
from automata_api.agent.tools.router import ToolRouter
from automata_api.config import AgentConfig, ContextCompressionConfig


@dataclass
class MemoryStore:
    recent_messages: list[dict]
    context_messages: list[dict] | None = None

    def get_recent_messages(self, session_id: str, limit: int) -> list[dict]:
        return self.recent_messages[-limit:]

    def get_messages_after_sequence(self, session_id: str, sequence: int) -> list[dict]:
        return []

    def get_recent_context_messages(self, session_id: str, limit: int) -> list[dict]:
        return (self.context_messages or [])[-limit:]

    def get_context_messages_after_sequence(
        self, session_id: str, sequence: int
    ) -> list[dict]:
        return [
            row
            for row in self.context_messages or []
            if int(row["sequence"]) > sequence
        ]

    def save_context_message(self, session_id: str, message: dict) -> dict:
        if self.context_messages is None:
            self.context_messages = []
        row = {
            "message": message,
            "sequence": len(self.context_messages) + 1,
        }
        self.context_messages.append(row)
        return row

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


async def collect_events(events):
    return [event async for event in events]


class RuntimeEchoTool(AgentTool):
    name = "calendar_lookup"
    read_only = True

    def spec(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "Lookup calendar events and meetings.",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
            },
        }

    async def run(self, arguments):
        return ToolResult(
            name=self.name,
            arguments=arguments,
            content=json.dumps({"ok": True, "value": arguments.get("value")}),
            success=True,
        )


def test_stream_agent_loop_yields_tokens_final_and_injects_approved_plan(monkeypatch):
    configure_runtime(monkeypatch)
    calls = []

    async def fake_stream_chat_completion(messages, tools=None):
        calls.append({"messages": messages, "tools": tools})
        yield {"content": "streamed "}
        yield {"content": "done"}

    monkeypatch.setattr(llm, "stream_chat_completion", fake_stream_chat_completion)

    events = asyncio.run(
        collect_events(
            runtime.stream_agent_loop(
                session_id="session-1",
                store=MemoryStore(
                    recent_messages=[{"role": "user", "content": "implement it"}]
                ),
                workspace="workspace",
                approved_plan_content="Approved plan body",
            )
        )
    )

    assert [event["type"] for event in events] == [
        "agent_step",
        "token",
        "token",
        "final",
    ]
    assert "".join(
        event.get("content", "") for event in events if event["type"] == "token"
    ) == "streamed done"
    assert events[-1] == {"type": "final", "content": "streamed done", "mode": "act"}
    assert calls[0]["messages"][0]["role"] == "system"
    assert "Current workspace: workspace" in calls[0]["messages"][0]["content"]
    assert "Approved plan body" in calls[0]["messages"][1]["content"]
    assert calls[0]["messages"][2] == {"role": "user", "content": "implement it"}
    assert {tool["function"]["name"] for tool in calls[0]["tools"]} >= {
        "exec_command",
        "run_bash",
        "write_file",
        "apply_patch",
    }


def test_stream_plan_loop_yields_tokens_final_and_plan_tools(monkeypatch):
    configure_runtime(monkeypatch)
    calls = []

    async def fake_stream_chat_completion(messages, tools=None):
        calls.append({"messages": messages, "tools": tools})
        yield {"content": "# Plan\n"}
        yield {"content": "\n1. Inspect."}

    monkeypatch.setattr(llm, "stream_chat_completion", fake_stream_chat_completion)

    events = asyncio.run(
        collect_events(
            runtime.stream_plan_loop(
                session_id="session-1",
                store=MemoryStore(recent_messages=[]),
                workspace="workspace",
            )
        )
    )

    assert [event["type"] for event in events] == [
        "agent_step",
        "token",
        "token",
        "final",
    ]
    assert events[0]["mode"] == "plan"
    assert events[-1] == {"type": "final", "content": "# Plan\n\n1. Inspect.", "mode": "plan"}
    tool_names = {tool["function"]["name"] for tool in calls[0]["tools"]}
    assert tool_names == runtime.PLAN_TOOL_NAMES
    assert "backend Plan mode" in calls[0]["messages"][0]["content"]
    assert "Current workspace: workspace" in calls[0]["messages"][0]["content"]


def test_stream_model_loop_yields_tokens_and_final(monkeypatch):
    async def fake_stream_chat_completion(messages, tools=None):
        assert messages == [{"role": "user", "content": "hello"}]
        assert tools == []
        yield {"content": "final "}
        yield {"content": "answer"}

    monkeypatch.setattr(llm, "stream_chat_completion", fake_stream_chat_completion)

    async def collect():
        return [
            event
            async for event in runtime.stream_model_loop(
                messages=[{"role": "user", "content": "hello"}],
                tools=[],
                compression_config=ContextCompressionConfig(False, 1_000, 100),
                model="unit-model",
                mode="act",
                allowed_tool_names=None,
                workspace="workspace",
            )
        ]

    events = asyncio.run(collect())

    assert [event["type"] for event in events] == [
        "agent_step",
        "token",
        "token",
        "final",
    ]
    assert "".join(
        event.get("content", "") for event in events if event["type"] == "token"
    ) == "final answer"
    assert events[-1] == {"type": "final", "content": "final answer", "mode": "act"}


def test_stream_model_loop_accumulates_split_tool_call_then_streams_final(monkeypatch):
    calls = []

    async def fake_stream_chat_completion(messages, tools=None):
        calls.append(list(messages))
        if len(calls) == 1:
            yield {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_split",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": '},
                    }
                ]
            }
            yield {
                "tool_calls": [
                    {
                        "index": 0,
                        "function": {"arguments": '"README.md"}'},
                    }
                ]
            }
            return

        assert messages[-2]["role"] == "assistant"
        assert messages[-2]["tool_calls"][0]["function"] == {
            "name": "read_file",
            "arguments": '{"path": "README.md"}',
        }
        assert messages[-1]["role"] == "tool"
        assert messages[-1]["tool_call_id"] == "call_split"
        yield {"content": "after "}
        yield {"content": "tool"}

    async def fake_run_tool(name, arguments, workspace):
        assert name == "read_file"
        assert arguments == '{"path": "README.md"}'
        assert workspace == "workspace"
        return ToolResult(name=name, arguments={}, content='{"ok": true}', success=True)

    monkeypatch.setattr(llm, "stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr(runtime, "run_tool", fake_run_tool)

    events = asyncio.run(
        collect_events(
            runtime.stream_model_loop(
                messages=[{"role": "user", "content": "inspect"}],
                tools=[],
                compression_config=ContextCompressionConfig(False, 1_000, 100),
                model="unit-model",
                mode="act",
                allowed_tool_names=None,
                workspace="workspace",
            )
        )
    )

    assert [event["type"] for event in events] == [
        "agent_step",
        "tool_call",
        "tool_result",
        "agent_step",
        "token",
        "token",
        "final",
    ]
    assert events[1] == {
        "type": "tool_call",
        "tool_call_id": "call_split",
        "tool": "read_file",
        "arguments": '{"path": "README.md"}',
    }
    assert events[-1] == {"type": "final", "content": "after tool", "mode": "act"}
    assert len(calls) == 2


def test_stream_model_loop_refreshes_tools_after_tool_search(monkeypatch):
    calls = []
    router = ToolRouter(
        [
            descriptor_for_tool(
                RuntimeEchoTool(),
                exposure=ToolExposure.DEFERRED,
                source="unit-test",
            )
        ]
    )

    async def fake_stream_chat_completion(messages, tools=None):
        tool_names = {tool["function"]["name"] for tool in tools or []}
        calls.append(tool_names)
        if len(calls) == 1:
            assert tool_names == {"tool_search"}
            yield {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_search",
                        "type": "function",
                        "function": {
                            "name": "tool_search",
                            "arguments": '{"query": "calendar meeting"}',
                        },
                    }
                ]
            }
            return

        if len(calls) == 2:
            assert tool_names == {"calendar_lookup"}
            yield {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_calendar",
                        "type": "function",
                        "function": {
                            "name": "calendar_lookup",
                            "arguments": '{"value": "today"}',
                        },
                    }
                ]
            }
            return

        assert tool_names == {"calendar_lookup"}
        assert messages[-1]["role"] == "tool"
        assert messages[-1]["tool_call_id"] == "call_calendar"
        yield {"content": "calendar done"}

    monkeypatch.setattr(llm, "stream_chat_completion", fake_stream_chat_completion)

    events = asyncio.run(
        collect_events(
            runtime.stream_model_loop(
                messages=[{"role": "user", "content": "inspect calendar"}],
                router=router,
                compression_config=ContextCompressionConfig(False, 1_000, 100),
                model="unit-model",
                mode="act",
                allowed_tool_names=None,
                workspace="workspace",
            )
        )
    )

    assert [event["type"] for event in events] == [
        "agent_step",
        "tool_call",
        "tool_result",
        "agent_step",
        "tool_call",
        "tool_result",
        "agent_step",
        "token",
        "final",
    ]
    assert events[1]["tool"] == "tool_search"
    assert json.loads(events[2]["content"])["activated_tools"] == ["calendar_lookup"]
    assert events[4]["tool"] == "calendar_lookup"
    assert events[-1] == {"type": "final", "content": "calendar done", "mode": "act"}
    assert calls == [{"tool_search"}, {"calendar_lookup"}, {"calendar_lookup"}]


def test_stream_model_loop_does_not_emit_tool_turn_content_as_token(monkeypatch):
    async def fake_stream_chat_completion(messages, tools=None):
        if any(message.get("role") == "tool" for message in messages):
            yield {"content": "final"}
            return

        yield {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_read",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ]
        }
        yield {"content": "internal tool preface"}

    async def fake_run_tool(name, arguments, workspace):
        return ToolResult(name=name, arguments={}, content='{"ok": true}', success=True)

    monkeypatch.setattr(llm, "stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr(runtime, "run_tool", fake_run_tool)

    events = asyncio.run(
        collect_events(
            runtime.stream_model_loop(
                messages=[{"role": "user", "content": "inspect"}],
                tools=[],
                compression_config=ContextCompressionConfig(False, 1_000, 100),
                model="unit-model",
                mode="act",
                allowed_tool_names=None,
                workspace="workspace",
            )
        )
    )

    token_text = "".join(
        event.get("content", "") for event in events if event["type"] == "token"
    )
    assert token_text == "final"
    assert [event["type"] for event in events] == [
        "agent_step",
        "tool_call",
        "tool_result",
        "agent_step",
        "token",
        "final",
    ]


def test_stream_model_loop_rejects_empty_response(monkeypatch):
    async def fake_stream_chat_completion(messages, tools=None):
        yield {"content": "  "}

    monkeypatch.setattr(llm, "stream_chat_completion", fake_stream_chat_completion)

    with pytest.raises(llm.AgentProviderError, match="empty response"):
        asyncio.run(
            collect_events(
                runtime.stream_model_loop(
                    messages=[],
                    tools=[],
                    compression_config=ContextCompressionConfig(False, 1_000, 100),
                    model="unit-model",
                    mode="act",
                    allowed_tool_names=None,
                    workspace="workspace",
                )
            )
        )


def test_stream_model_loop_rejects_max_steps(monkeypatch):
    async def fake_stream_chat_completion(messages, tools=None):
        yield {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_repeat",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ]
        }

    async def fake_run_tool(name, arguments, workspace):
        return ToolResult(name=name, arguments={}, content="{}", success=True)

    monkeypatch.setattr(llm, "stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr(runtime, "run_tool", fake_run_tool)

    with pytest.raises(llm.AgentProviderError, match="maximum step limit"):
        asyncio.run(
            collect_events(
                runtime.stream_model_loop(
                    messages=[],
                    tools=[],
                    compression_config=ContextCompressionConfig(False, 1_000, 100),
                    model="unit-model",
                    mode="act",
                    allowed_tool_names=None,
                    workspace="workspace",
                )
            )
        )


def test_stream_execute_tool_call_yields_events_and_appends_provider_result(monkeypatch):
    async def fake_run_tool(name, arguments, workspace):
        assert name == "read_file"
        assert arguments == '{"path": "README.md"}'
        assert workspace == "workspace"
        return ToolResult(
            name=name,
            arguments={"path": "README.md"},
            content='{"content": "ok"}',
            success=True,
        )

    monkeypatch.setattr(runtime, "run_tool", fake_run_tool)

    messages = []
    events = asyncio.run(
        collect_events(
            runtime.stream_execute_tool_call(
                messages=messages,
                tool_call={
                    "id": "call_read",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path": "README.md"}',
                    },
                },
                workspace="workspace",
            )
        )
    )

    assert events == [
        {
            "type": "tool_call",
            "tool_call_id": "call_read",
            "tool": "read_file",
            "arguments": '{"path": "README.md"}',
        },
        {
            "type": "tool_result",
            "tool_call_id": "call_read",
            "tool": "read_file",
            "success": True,
            "content": '{"content": "ok"}',
        },
    ]
    assert messages == [
        {
            "role": "tool",
            "tool_call_id": "call_read",
            "content": '{"content": "ok"}',
        }
    ]


def test_stream_execute_tool_call_blocks_disallowed_plan_tool(monkeypatch):
    async def fail_run_tool(name, arguments, workspace):
        raise AssertionError("blocked tools must not execute")

    monkeypatch.setattr(runtime, "run_tool", fail_run_tool)

    messages = []
    events = asyncio.run(
        collect_events(
            runtime.stream_execute_tool_call(
                messages=messages,
                tool_call={
                    "id": "call_write",
                    "type": "function",
                    "function": {"name": "write_file", "arguments": '{"path": "x"}'},
                },
                workspace="workspace",
                mode="plan",
                allowed_tool_names={"read_file"},
            )
        )
    )

    result = json.loads(messages[-1]["content"])
    assert events[0]["type"] == "tool_call"
    assert events[1]["success"] is False
    assert result["error"] == "blocked_by_plan_mode"
    assert result["allowed_tools"] == ["read_file"]


@pytest.mark.parametrize(
    "tool_call, message",
    [
        ({}, "invalid tool call"),
        ({"function": {"arguments": "{}"}}, "without a name"),
    ],
)
def test_stream_execute_tool_call_rejects_invalid_tool_call(tool_call, message):
    with pytest.raises(llm.AgentProviderError, match=message):
        asyncio.run(
            collect_events(
                runtime.stream_execute_tool_call(
                    messages=[], tool_call=tool_call, workspace="workspace"
                )
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
        "content": "ok",
    }
    assert json.loads(blocked.content)["allowed_tools"] == ["read_file", "rg"]
    assert filtered == [{"function": {"name": "read_file"}}]
    assert runtime.tool_name({"function": {"name": ""}}) is None
    assert runtime.tool_name({"function": "not-a-dict"}) is None
