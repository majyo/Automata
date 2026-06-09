import asyncio
import json
from dataclasses import dataclass

import pytest

from automata_api.agent import llm, prompts, runtime
from automata_api.agent.tools import ToolResult
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
        row = {"message": message, "sequence": len(self.context_messages) + 1}
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


def configure_runtime(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime,
        "get_agent_config",
        lambda: AgentConfig(
            api_key="test-key",
            base_url="https://provider.test",
            model="plan-unit-model",
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


def event_types(events: list[dict]) -> list[str]:
    return [event["type"] for event in events]


def plan_tool_arguments(tool_name: str) -> str:
    return {
        "read_file": '{"path": "README.md"}',
        "rg": '{"pattern": "needle", "path": "."}',
        "grep": '{"pattern": "needle", "path": "."}',
        "apply_patch_preview": '{"patch": "--- a/x\\n+++ b/x\\n"}',
    }[tool_name]


def test_plan_tool_allowlist_matches_registered_tools_and_prompt():
    registered_names = {tool["function"]["name"] for tool in runtime.tool_specs()}
    prompt = prompts.plan_system_prompt("workspace")

    assert runtime.PLAN_TOOL_NAMES <= registered_names
    assert runtime.PLAN_TOOL_NAMES == {
        "read_file",
        "rg",
        "grep",
        "apply_patch_preview",
    }
    for tool_name in runtime.PLAN_TOOL_NAMES:
        assert tool_name in prompt
    for blocked_tool in {"run_bash", "write_file", "apply_patch"}:
        assert blocked_tool in registered_names
        assert blocked_tool not in runtime.PLAN_TOOL_NAMES
        assert blocked_tool in prompt


def test_plan_loop_exposes_only_plan_tools(monkeypatch):
    configure_runtime(monkeypatch)
    calls = []

    async def fake_stream_chat_completion(messages, tools=None):
        calls.append({"messages": messages, "tools": tools})
        yield {"content": "# Plan\n\n1. Inspect only."}

    monkeypatch.setattr(llm, "stream_chat_completion", fake_stream_chat_completion)

    events = asyncio.run(
        collect_events(
            runtime.stream_plan_loop(
                session_id="session-1",
                store=MemoryStore(
                    recent_messages=[{"role": "user", "content": "plan it"}]
                ),
                workspace="workspace",
            )
        )
    )

    tool_names = {tool["function"]["name"] for tool in calls[0]["tools"]}
    assert tool_names == runtime.PLAN_TOOL_NAMES
    assert tool_names == {"read_file", "rg", "grep", "apply_patch_preview"}
    assert {"run_bash", "write_file", "apply_patch"}.isdisjoint(tool_names)
    assert "backend Plan mode" in calls[0]["messages"][0]["content"]
    assert events[-1] == {
        "type": "final",
        "content": "# Plan\n\n1. Inspect only.",
        "mode": "plan",
    }


def test_plan_loop_final_response_saves_assistant_context(monkeypatch):
    configure_runtime(monkeypatch)
    store = MemoryStore(recent_messages=[{"role": "user", "content": "plan it"}])

    async def fake_stream_chat_completion(messages, tools=None):
        yield {"content": "# Plan\n\n1. Inspect.\n2. Report."}

    monkeypatch.setattr(llm, "stream_chat_completion", fake_stream_chat_completion)

    events = asyncio.run(
        collect_events(
            runtime.stream_plan_loop(
                session_id="session-1",
                store=store,
                workspace="workspace",
            )
        )
    )

    assert events[-1]["mode"] == "plan"
    assert store.context_messages == [
        {
            "message": {
                "role": "assistant",
                "content": "# Plan\n\n1. Inspect.\n2. Report.",
            },
            "sequence": 1,
        }
    ]


@pytest.mark.parametrize("tool_name", sorted(runtime.PLAN_TOOL_NAMES))
def test_plan_loop_executes_every_allowed_plan_tool(monkeypatch, tool_name):
    configure_runtime(monkeypatch)
    tool_runs = []

    async def fake_stream_chat_completion(messages, tools=None):
        if not any(message.get("role") == "tool" for message in messages):
            yield {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": f"call_{tool_name}",
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": plan_tool_arguments(tool_name),
                        },
                    }
                ]
            }
            return

        assert messages[-1]["role"] == "tool"
        assert messages[-1]["tool_call_id"] == f"call_{tool_name}"
        yield {"content": f"Plan after {tool_name}."}

    async def fake_run_tool(name, arguments, workspace):
        tool_runs.append((name, arguments, workspace))
        return ToolResult(
            name=name,
            arguments={},
            content=json.dumps({"ok": True, "tool": name}),
            success=True,
        )

    monkeypatch.setattr(llm, "stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr(runtime, "run_tool", fake_run_tool)

    events = asyncio.run(
        collect_events(
            runtime.stream_plan_loop(
                session_id="session-1",
                store=MemoryStore(recent_messages=[]),
                workspace="workspace",
            )
        )
    )

    assert event_types(events) == [
        "agent_step",
        "tool_call",
        "tool_result",
        "agent_step",
        "token",
        "final",
    ]
    assert events[1]["tool"] == tool_name
    assert events[2]["tool"] == tool_name
    assert events[2]["success"] is True
    assert events[-1] == {
        "type": "final",
        "content": f"Plan after {tool_name}.",
        "mode": "plan",
    }
    assert tool_runs == [(tool_name, plan_tool_arguments(tool_name), "workspace")]


def test_plan_loop_runs_allowed_preview_tool_and_keeps_plan_mode(monkeypatch):
    configure_runtime(monkeypatch)
    calls = []
    tool_runs = []
    store = MemoryStore(recent_messages=[{"role": "user", "content": "plan patch"}])

    async def fake_stream_chat_completion(messages, tools=None):
        calls.append({"messages": list(messages), "tools": tools})
        if len(calls) == 1:
            yield {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_preview",
                        "type": "function",
                        "function": {
                            "name": "apply_patch_preview",
                            "arguments": '{"patch": "--- a/x\\n+++ b/x\\n"}',
                        },
                    }
                ]
            }
            return

        assert calls[-1]["messages"][-2]["role"] == "assistant"
        assert calls[-1]["messages"][-1]["role"] == "tool"
        yield {"content": "Plan after preview."}

    async def fake_run_tool(name, arguments, workspace):
        tool_runs.append((name, arguments, workspace))
        return ToolResult(
            name=name,
            arguments={"patch": "--- a/x\n+++ b/x\n"},
            content='{"ok": true, "dry_run": true}',
            success=True,
        )

    monkeypatch.setattr(llm, "stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr(runtime, "run_tool", fake_run_tool)

    events = asyncio.run(
        collect_events(
            runtime.stream_plan_loop(
                session_id="session-1",
                store=store,
                workspace="workspace",
            )
        )
    )

    assert event_types(events) == [
        "agent_step",
        "tool_call",
        "tool_result",
        "agent_step",
        "token",
        "final",
    ]
    assert events[0]["mode"] == "plan"
    assert events[3]["mode"] == "plan"
    assert tool_runs == [
        ("apply_patch_preview", '{"patch": "--- a/x\\n+++ b/x\\n"}', "workspace")
    ]
    assert events[2]["success"] is True
    assert events[-1] == {
        "type": "final",
        "content": "Plan after preview.",
        "mode": "plan",
    }
    assert [row["message"]["role"] for row in store.context_messages or []] == [
        "assistant",
        "tool",
        "assistant",
    ]


def test_plan_loop_executes_multiple_allowed_tool_calls_in_one_turn(monkeypatch):
    configure_runtime(monkeypatch)
    calls = []
    tool_runs = []

    async def fake_stream_chat_completion(messages, tools=None):
        calls.append(list(messages))
        if len(calls) == 1:
            yield {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_read",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "README.md"}',
                        },
                    },
                    {
                        "index": 1,
                        "id": "call_rg",
                        "type": "function",
                        "function": {
                            "name": "rg",
                            "arguments": '{"pattern": "TODO", "path": "."}',
                        },
                    },
                ]
            }
            return

        assert [message["role"] for message in messages[-3:]] == [
            "assistant",
            "tool",
            "tool",
        ]
        assert messages[-2]["tool_call_id"] == "call_read"
        assert messages[-1]["tool_call_id"] == "call_rg"
        yield {"content": "Plan after both inspections."}

    async def fake_run_tool(name, arguments, workspace):
        tool_runs.append((name, arguments, workspace))
        return ToolResult(
            name=name,
            arguments={},
            content=json.dumps({"ok": True, "tool": name}),
            success=True,
        )

    monkeypatch.setattr(llm, "stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr(runtime, "run_tool", fake_run_tool)

    events = asyncio.run(
        collect_events(
            runtime.stream_plan_loop(
                session_id="session-1",
                store=MemoryStore(recent_messages=[]),
                workspace="workspace",
            )
        )
    )

    assert event_types(events) == [
        "agent_step",
        "tool_call",
        "tool_result",
        "tool_call",
        "tool_result",
        "agent_step",
        "token",
        "final",
    ]
    assert [event["tool"] for event in events if event["type"] == "tool_call"] == [
        "read_file",
        "rg",
    ]
    assert tool_runs == [
        ("read_file", '{"path": "README.md"}', "workspace"),
        ("rg", '{"pattern": "TODO", "path": "."}', "workspace"),
    ]
    assert events[-1]["content"] == "Plan after both inspections."


@pytest.mark.parametrize("tool_name", ["run_bash", "write_file", "apply_patch"])
def test_plan_loop_blocks_every_mutating_tool(monkeypatch, tool_name):
    configure_runtime(monkeypatch)

    async def fake_stream_chat_completion(messages, tools=None):
        if not any(message.get("role") == "tool" for message in messages):
            yield {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": f"call_{tool_name}",
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": "{}",
                        },
                    }
                ]
            }
            return

        result = json.loads(messages[-1]["content"])
        assert result["error"] == "blocked_by_plan_mode"
        assert result["tool"] == tool_name
        yield {"content": f"Plan after blocked {tool_name}."}

    async def fail_run_tool(name, arguments, workspace):
        raise AssertionError(f"{name} must be blocked in plan mode")

    monkeypatch.setattr(llm, "stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr(runtime, "run_tool", fail_run_tool)

    events = asyncio.run(
        collect_events(
            runtime.stream_plan_loop(
                session_id="session-1",
                store=MemoryStore(recent_messages=[]),
                workspace="workspace",
            )
        )
    )

    blocked_result = json.loads(events[2]["content"])
    assert events[1]["tool"] == tool_name
    assert events[2]["success"] is False
    assert blocked_result["error"] == "blocked_by_plan_mode"
    assert blocked_result["allowed_tools"] == sorted(runtime.PLAN_TOOL_NAMES)
    assert events[-1] == {
        "type": "final",
        "content": f"Plan after blocked {tool_name}.",
        "mode": "plan",
    }


def test_plan_loop_blocks_unlisted_tool_even_if_model_requests_it(monkeypatch):
    configure_runtime(monkeypatch)
    store = MemoryStore(recent_messages=[{"role": "user", "content": "plan write"}])

    async def fake_stream_chat_completion(messages, tools=None):
        if not any(message.get("role") == "tool" for message in messages):
            yield {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_write",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": '{"path": "x.txt", "content": "no"}',
                        },
                    }
                ]
            }
            return

        result = json.loads(messages[-1]["content"])
        assert result["error"] == "blocked_by_plan_mode"
        assert result["mode"] == "plan"
        assert result["allowed_tools"] == sorted(runtime.PLAN_TOOL_NAMES)
        yield {"content": "Plan after blocked write."}

    async def fail_run_tool(name, arguments, workspace):
        raise AssertionError("blocked plan-mode tools must not execute")

    monkeypatch.setattr(llm, "stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr(runtime, "run_tool", fail_run_tool)

    events = asyncio.run(
        collect_events(
            runtime.stream_plan_loop(
                session_id="session-1",
                store=store,
                workspace="workspace",
            )
        )
    )

    blocked_result = json.loads(events[2]["content"])
    assert event_types(events) == [
        "agent_step",
        "tool_call",
        "tool_result",
        "agent_step",
        "token",
        "final",
    ]
    assert events[1]["tool"] == "write_file"
    assert events[2]["success"] is False
    assert blocked_result["error"] == "blocked_by_plan_mode"
    assert events[-1]["mode"] == "plan"
    assert json.loads((store.context_messages or [])[1]["message"]["content"])[
        "error"
    ] == "blocked_by_plan_mode"


def test_plan_loop_blocks_unknown_tool_before_registry_lookup(monkeypatch):
    configure_runtime(monkeypatch)

    async def fake_stream_chat_completion(messages, tools=None):
        if not any(message.get("role") == "tool" for message in messages):
            yield {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_unknown",
                        "type": "function",
                        "function": {
                            "name": "delete_file",
                            "arguments": '{"path": "README.md"}',
                        },
                    }
                ]
            }
            return

        yield {"content": "Plan recovered from unknown tool."}

    async def fail_run_tool(name, arguments, workspace):
        raise AssertionError("unknown tools must be blocked before registry lookup")

    monkeypatch.setattr(llm, "stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr(runtime, "run_tool", fail_run_tool)

    events = asyncio.run(
        collect_events(
            runtime.stream_plan_loop(
                session_id="session-1",
                store=MemoryStore(recent_messages=[]),
                workspace="workspace",
            )
        )
    )

    blocked_result = json.loads(events[2]["content"])
    assert events[1]["tool"] == "delete_file"
    assert events[2]["success"] is False
    assert blocked_result["tool"] == "delete_file"
    assert blocked_result["error"] == "blocked_by_plan_mode"
    assert events[-1]["content"] == "Plan recovered from unknown tool."


def test_plan_loop_persists_blocked_tool_protocol_context(monkeypatch):
    configure_runtime(monkeypatch)
    store = MemoryStore(recent_messages=[{"role": "user", "content": "plan write"}])

    async def fake_stream_chat_completion(messages, tools=None):
        if not any(message.get("role") == "tool" for message in messages):
            yield {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_write",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": '{"path": "x.txt"}',
                        },
                    }
                ]
            }
            return

        yield {"content": "Plan after persisted block."}

    async def fail_run_tool(name, arguments, workspace):
        raise AssertionError("blocked tool should not run")

    monkeypatch.setattr(llm, "stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr(runtime, "run_tool", fail_run_tool)

    asyncio.run(
        collect_events(
            runtime.stream_plan_loop(
                session_id="session-1",
                store=store,
                workspace="workspace",
            )
        )
    )

    saved_messages = [row["message"] for row in store.context_messages or []]
    assert [message["role"] for message in saved_messages] == [
        "assistant",
        "tool",
        "assistant",
    ]
    assert saved_messages[0]["content"] is None
    assert saved_messages[0]["tool_calls"][0]["id"] == "call_write"
    assert saved_messages[1]["tool_call_id"] == "call_write"
    assert json.loads(saved_messages[1]["content"])["error"] == "blocked_by_plan_mode"
    assert saved_messages[2] == {
        "role": "assistant",
        "content": "Plan after persisted block.",
    }


def test_blocked_plan_tool_result_payload_is_stable():
    result = runtime.blocked_tool_result(
        "run_bash",
        {"command": "echo no"},
        "plan",
        {"rg", "read_file"},
    )

    payload = json.loads(result.content)
    assert result.name == "run_bash"
    assert result.arguments == {"command": "echo no"}
    assert result.success is False
    assert payload == {
        "simulated": False,
        "ok": False,
        "tool": "run_bash",
        "mode": "plan",
        "error": "blocked_by_plan_mode",
        "allowed_tools": ["read_file", "rg"],
    }


def test_approved_plan_message_is_inserted_before_recent_history(monkeypatch):
    configure_runtime(monkeypatch)
    calls = []

    async def fake_stream_chat_completion(messages, tools=None):
        calls.append(messages)
        yield {"content": "Done."}

    monkeypatch.setattr(llm, "stream_chat_completion", fake_stream_chat_completion)

    events = asyncio.run(
        collect_events(
            runtime.stream_agent_loop(
                session_id="session-1",
                store=MemoryStore(
                    recent_messages=[{"role": "user", "content": "execute"}]
                ),
                workspace="workspace",
                approved_plan_content="1. Implement the approved change.",
            )
        )
    )

    assert [message["role"] for message in calls[0][:3]] == [
        "system",
        "system",
        "user",
    ]
    assert "Current workspace: workspace" in calls[0][0]["content"]
    assert "approved the following implementation plan" in calls[0][1]["content"]
    assert "1. Implement the approved change." in calls[0][1]["content"]
    assert calls[0][2] == {"role": "user", "content": "execute"}
    assert events[-1] == {"type": "final", "content": "Done.", "mode": "act"}


def test_approved_plan_execution_uses_act_mode_and_allows_mutating_tools(monkeypatch):
    configure_runtime(monkeypatch)
    calls = []
    tool_runs = []

    async def fake_stream_chat_completion(messages, tools=None):
        calls.append({"messages": list(messages), "tools": tools})
        if len(calls) == 1:
            yield {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_write",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": '{"path": "done.txt", "content": "ok"}',
                        },
                    }
                ]
            }
            return

        yield {"content": "Executed approved plan."}

    async def fake_run_tool(name, arguments, workspace):
        tool_runs.append((name, arguments, workspace))
        return ToolResult(
            name=name,
            arguments={"path": "done.txt"},
            content='{"ok": true, "simulated": false}',
            success=True,
        )

    monkeypatch.setattr(llm, "stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr(runtime, "run_tool", fake_run_tool)

    events = asyncio.run(
        collect_events(
            runtime.stream_agent_loop(
                session_id="session-1",
                store=MemoryStore(
                    recent_messages=[{"role": "user", "content": "execute it"}]
                ),
                workspace="workspace",
                approved_plan_content="1. Write done.txt",
            )
        )
    )

    tool_names = {tool["function"]["name"] for tool in calls[0]["tools"]}
    assert "write_file" in tool_names
    assert "apply_patch" in tool_names
    assert any(
        message["role"] == "system" and "1. Write done.txt" in message["content"]
        for message in calls[0]["messages"]
    )
    assert event_types(events) == [
        "agent_step",
        "tool_call",
        "tool_result",
        "agent_step",
        "token",
        "final",
    ]
    assert events[0]["mode"] == "act"
    assert events[3]["mode"] == "act"
    assert tool_runs == [
        ("write_file", '{"path": "done.txt", "content": "ok"}', "workspace")
    ]
    assert events[2]["success"] is True
    assert events[-1] == {
        "type": "final",
        "content": "Executed approved plan.",
        "mode": "act",
    }
