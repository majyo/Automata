import asyncio
import json

from automata_api.config import ContextCompressionConfig
from automata_api.agent import context as agent_context
from automata_api.agent import llm, runtime
from automata_api.agent.tools import ToolResult
from automata_api.repositories.agent_store import SessionAgentContextStore
from automata_api.repositories.sessions import (
    fetch_context_summary,
    save_message,
)


def compact_event_types(events):
    compacted = []
    previous_was_token = False
    for event in events:
        event_type = event["type"]
        if event_type == "token":
            if not previous_was_token:
                compacted.append(event_type)
            previous_was_token = True
            continue

        compacted.append(event_type)
        previous_was_token = False

    return compacted


def token_content(events):
    return "".join(
        event.get("content", "") for event in events if event["type"] == "token"
    )


def stream_from_completion(create_response):
    async def fake_stream_chat_completion(messages, tools=None):
        response = await create_response(messages, tools)
        delta = {}
        content = response.get("content")
        if isinstance(content, str) and content:
            delta["content"] = content

        tool_calls = response.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            delta["tool_calls"] = [
                {
                    "index": index,
                    "id": tool_call.get("id", f"call_{index}"),
                    "type": tool_call.get("type", "function"),
                    "function": tool_call.get("function", {}),
                }
                for index, tool_call in enumerate(tool_calls)
            ]

        if delta:
            yield delta

    return fake_stream_chat_completion


class CapturingWebSocket:
    def __init__(self):
        self.events = []

    async def send_json(self, event):
        self.events.append(event)


def test_fetch_agent_context_compresses_long_history(client, monkeypatch):
    session = client.post("/sessions", json={"title": "Long History"}).json()
    for index in range(10):
        save_message(
            session_id=session["id"],
            role="user" if index % 2 == 0 else "agent",
            content=f"message-{index} " + ("x" * 220),
        )

    calls = []

    async def fake_create_llm_response(messages, tools=None):
        calls.append({"messages": messages, "tools": tools})
        assert tools is None
        return {"role": "assistant", "content": "summary keeps the important bits"}

    monkeypatch.setattr(llm, "create_llm_response", fake_create_llm_response)

    websocket = CapturingWebSocket()
    messages = asyncio.run(
        agent_context.fetch_agent_context(
            emit_event=websocket.send_json,
            session_id=session["id"],
            store=SessionAgentContextStore(),
            compression_config=ContextCompressionConfig(
                enabled=True,
                threshold_chars=1_200,
                target_chars=250,
            ),
        )
    )

    stored = fetch_context_summary(session["id"])
    assert stored["content"] == "summary keeps the important bits"
    assert stored["through_sequence"] == 2
    assert len(calls) == 1
    assert messages[1]["role"] == "system"
    assert "summary keeps the important bits" in messages[1]["content"]
    assert messages[2]["content"].startswith("message-2 ")
    assert messages[-1]["content"].startswith("message-9 ")
    assert len(messages) == 10
    assert websocket.events[0]["type"] == "context_compressed"
    assert websocket.events[0]["scope"] == "history"
    assert websocket.events[0]["compressed_messages"] == 2
    assert websocket.events[0]["through_sequence"] == 2


def test_fetch_agent_context_skips_summary_when_under_threshold(client, monkeypatch):
    session = client.post("/sessions", json={"title": "Short History"}).json()
    for index in range(3):
        save_message(
            session_id=session["id"],
            role="user",
            content=f"short-{index}",
        )

    async def fake_create_llm_response(messages, tools=None):
        raise AssertionError("summary LLM should not be called")

    monkeypatch.setattr(llm, "create_llm_response", fake_create_llm_response)

    websocket = CapturingWebSocket()
    messages = asyncio.run(
        agent_context.fetch_agent_context(
            emit_event=websocket.send_json,
            session_id=session["id"],
            store=SessionAgentContextStore(),
            compression_config=ContextCompressionConfig(
                enabled=True,
                threshold_chars=100_000,
                target_chars=1_000,
            ),
        )
    )

    assert fetch_context_summary(session["id"]) is None
    assert [message["content"] for message in messages[1:]] == [
        "short-0",
        "short-1",
        "short-2",
    ]
    assert websocket.events == []


def test_loop_context_compression_replaces_tool_protocol_messages(monkeypatch):
    calls = []

    async def fake_create_llm_response(messages, tools=None):
        calls.append({"messages": messages, "tools": tools})
        assert tools is None
        return {"role": "assistant", "content": "recent tool summary"}

    monkeypatch.setattr(llm, "create_llm_response", fake_create_llm_response)

    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "run a large tool"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "run_bash", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "run_bash",
            "content": "x" * 2_000,
        },
    ]

    websocket = CapturingWebSocket()
    compressed = asyncio.run(
        agent_context.compress_loop_context_if_needed(
            emit_event=websocket.send_json,
            messages=messages,
            compression_config=ContextCompressionConfig(
                enabled=True,
                threshold_chars=500,
                target_chars=100,
            ),
        )
    )

    assert len(calls) == 1
    assert all(message["role"] != "tool" for message in compressed)
    assert compressed[-1]["role"] == "system"
    assert "recent tool summary" in compressed[-1]["content"]
    assert websocket.events[0]["type"] == "context_compressed"
    assert websocket.events[0]["scope"] == "loop"
    assert websocket.events[0]["compressed_messages"] == 2


def test_chat_websocket_emits_history_compression_event(client, monkeypatch):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    monkeypatch.setenv("AUTOMATA_CONTEXT_COMPRESSION_THRESHOLD_CHARS", "1200")
    monkeypatch.setenv("AUTOMATA_CONTEXT_COMPRESSION_TARGET_CHARS", "250")
    session = client.post("/sessions", json={"title": "History Event"}).json()
    for index in range(10):
        save_message(
            session_id=session["id"],
            role="user",
            content=f"history-{index} " + ("x" * 220),
        )

    agent_calls = []

    async def fake_create_llm_response(messages, tools=None):
        if tools is None:
            return {"role": "assistant", "content": "compressed history summary"}

        agent_calls.append(messages)
        assert any(
            message["role"] == "system"
            and "compressed history summary" in message["content"]
            for message in messages
        )
        return {
            "role": "assistant",
            "content": "History compression finished.",
            "tool_calls": [],
        }

    monkeypatch.setattr(llm, "create_llm_response", fake_create_llm_response)
    monkeypatch.setattr(
        llm,
        "stream_chat_completion",
        stream_from_completion(fake_create_llm_response),
    )

    with client.websocket_connect("/ws/chat") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "prompt",
                "session_id": session["id"],
                "prompt": "continue",
            }
        )

        events = []
        while True:
            event = websocket.receive_json()
            events.append(event)
            if event["type"] in {"done", "error"}:
                break

    assert compact_event_types(events) == [
        "started",
        "context_compressed",
        "agent_step",
        "token",
        "done",
    ]
    assert events[1]["scope"] == "history"
    assert events[1]["compressed_messages"] == 3
    assert events[1]["through_sequence"] == 3
    assert token_content(events) == "History compression finished."
    assert len(agent_calls) == 1


def test_chat_websocket_emits_loop_compression_event(client, monkeypatch):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    monkeypatch.setenv("AUTOMATA_CONTEXT_COMPRESSION_THRESHOLD_CHARS", "500")
    monkeypatch.setenv("AUTOMATA_CONTEXT_COMPRESSION_TARGET_CHARS", "100")
    session = client.post("/sessions", json={"title": "Loop Event"}).json()
    agent_calls = []

    async def fake_create_llm_response(messages, tools=None):
        if tools is None:
            return {"role": "assistant", "content": "compressed tool summary"}

        agent_calls.append(messages)
        if len(agent_calls) == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_big",
                        "type": "function",
                        "function": {
                            "name": "run_bash",
                            "arguments": '{"command": "printf ok"}',
                        },
                    }
                ],
            }

        assert all(message["role"] != "tool" for message in messages)
        assert any(
            message["role"] == "system"
            and "compressed tool summary" in message["content"]
            for message in messages
        )
        return {
            "role": "assistant",
            "content": "Loop compression finished.",
            "tool_calls": [],
        }

    async def fake_run_tool(name, arguments, workspace):
        content = json.dumps(
            {
                "simulated": False,
                "ok": True,
                "stdout": "tool-output " + ("x" * 2_000),
            }
        )
        return ToolResult(name=name, arguments={}, content=content, success=True)

    monkeypatch.setattr(llm, "create_llm_response", fake_create_llm_response)
    monkeypatch.setattr(
        llm,
        "stream_chat_completion",
        stream_from_completion(fake_create_llm_response),
    )
    monkeypatch.setattr(runtime, "run_tool", fake_run_tool)

    with client.websocket_connect("/ws/chat") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "prompt",
                "session_id": session["id"],
                "prompt": "run a large tool",
            }
        )

        events = []
        while True:
            event = websocket.receive_json()
            events.append(event)
            if event["type"] in {"done", "error"}:
                break

    assert compact_event_types(events) == [
        "started",
        "agent_step",
        "tool_call",
        "tool_result",
        "context_compressed",
        "agent_step",
        "token",
        "done",
    ]
    assert events[4]["scope"] == "loop"
    assert events[4]["compressed_messages"] == 2
    assert token_content(events) == "Loop compression finished."
    assert len(agent_calls) == 2
