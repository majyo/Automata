import json

import pytest

from automata_api.services import tools


def test_chat_websocket_reports_missing_llm_config(client):
    session = client.post("/sessions", json={"title": "Chat"}).json()

    with client.websocket_connect("/ws/chat") as websocket:
        ready = websocket.receive_json()
        websocket.send_json(
            {
                "type": "prompt",
                "session_id": session["id"],
                "prompt": "hello",
            }
        )
        started = websocket.receive_json()
        error = websocket.receive_json()

    assert ready["type"] == "ready"
    assert "Missing AUTOMATA_LLM_API_KEY" in ready["message"]
    assert started == {
        "type": "started",
        "session_id": session["id"],
        "prompt": "hello",
    }
    assert error["type"] == "error"
    assert "Missing AUTOMATA_LLM_API_KEY" in error["message"]

    messages = client.get(f"/sessions/{session['id']}/messages").json()
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "hello"


def test_chat_websocket_runs_agent_loop_with_placeholder_tool(client, monkeypatch):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    session = client.post("/sessions", json={"title": "Agent"}).json()
    calls = []

    async def fake_create_llm_response(messages, tools=None):
        calls.append({"messages": list(messages), "tools": tools})
        if len(calls) == 1:
            assert tools is not None
            assert any(
                tool["function"]["name"] == "inspect_workspace" for tool in tools
            )
            return {
                "role": "assistant",
                "content": "",
                "reasoning_content": "Need to inspect the simulated workspace.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "inspect_workspace",
                            "arguments": '{"focus": "api"}',
                        },
                    }
                ],
            }

        assert messages[-2]["role"] == "assistant"
        assert messages[-2]["reasoning_content"] == "Need to inspect the simulated workspace."
        assert messages[-1]["role"] == "tool"
        assert messages[-1]["tool_call_id"] == "call_1"
        assert '"simulated": true' in messages[-1]["content"]
        return {
            "role": "assistant",
            "content": "I used a simulated workspace inspection and finished.",
            "tool_calls": [],
        }

    monkeypatch.setattr(
        "automata_api.services.agent.create_llm_response",
        fake_create_llm_response,
    )

    with client.websocket_connect("/ws/chat") as websocket:
        ready = websocket.receive_json()
        websocket.send_json(
            {
                "type": "prompt",
                "session_id": session["id"],
                "prompt": "inspect the workspace",
            }
        )

        events = []
        while True:
            event = websocket.receive_json()
            events.append(event)
            if event["type"] in {"done", "error"}:
                break

    assert ready["type"] == "ready"
    assert "DeepSeek agent ready" in ready["message"]
    assert [event["type"] for event in events] == [
        "started",
        "agent_step",
        "tool_call",
        "tool_result",
        "agent_step",
        "token",
        "done",
    ]
    assert events[2]["tool"] == "inspect_workspace"
    assert events[3]["success"] is True
    assert '"simulated": true' in events[3]["content"]
    assert events[5]["content"] == "I used a simulated workspace inspection and finished."

    messages = client.get(f"/sessions/{session['id']}/messages").json()
    assert [message["role"] for message in messages] == ["user", "agent"]
    assert messages[1]["content"] == "I used a simulated workspace inspection and finished."
    assert len(calls) == 2


def test_chat_websocket_runs_agent_loop_with_real_bash_tool(client, monkeypatch):
    if tools.resolve_bash_executable() is None:
        pytest.skip("bash is not available")

    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    session = client.post("/sessions", json={"title": "Bash Agent"}).json()
    calls = []

    async def fake_create_llm_response(messages, tools=None):
        calls.append({"messages": list(messages), "tools": tools})
        if len(calls) == 1:
            assert tools is not None
            assert any(tool["function"]["name"] == "run_bash" for tool in tools)
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_bash_1",
                        "type": "function",
                        "function": {
                            "name": "run_bash",
                            "arguments": (
                                '{"command": "printf agent-bash", '
                                '"timeout_seconds": 5}'
                            ),
                        },
                    }
                ],
            }

        assert messages[-1]["role"] == "tool"
        assert messages[-1]["tool_call_id"] == "call_bash_1"
        result = json.loads(messages[-1]["content"])
        assert result["simulated"] is False
        assert result["ok"] is True
        assert result["stdout"] == "agent-bash"
        return {
            "role": "assistant",
            "content": "Bash finished.",
            "tool_calls": [],
        }

    monkeypatch.setattr(
        "automata_api.services.agent.create_llm_response",
        fake_create_llm_response,
    )

    with client.websocket_connect("/ws/chat") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "prompt",
                "session_id": session["id"],
                "prompt": "run a bash check",
            }
        )

        events = []
        while True:
            event = websocket.receive_json()
            events.append(event)
            if event["type"] in {"done", "error"}:
                break

    assert [event["type"] for event in events] == [
        "started",
        "agent_step",
        "tool_call",
        "tool_result",
        "agent_step",
        "token",
        "done",
    ]
    assert events[2]["tool"] == "run_bash"
    assert events[3]["success"] is True
    assert '"simulated": false' in events[3]["content"]
    assert events[5]["content"] == "Bash finished."
    assert len(calls) == 2


def test_chat_websocket_runs_agent_loop_with_rg_tool(client, monkeypatch):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    session = client.post("/sessions", json={"title": "Search Agent"}).json()
    calls = []

    async def fake_create_llm_response(messages, tools=None):
        calls.append({"messages": list(messages), "tools": tools})
        if len(calls) == 1:
            assert tools is not None
            tool_names = {tool["function"]["name"] for tool in tools}
            assert "rg" in tool_names
            assert "grep" in tool_names
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_rg_1",
                        "type": "function",
                        "function": {
                            "name": "rg",
                            "arguments": (
                                '{"pattern": "async def run_tool", '
                                '"path": "api/automata_api/services/tools.py"}'
                            ),
                        },
                    }
                ],
            }

        assert messages[-1]["role"] == "tool"
        assert messages[-1]["tool_call_id"] == "call_rg_1"
        result = json.loads(messages[-1]["content"])
        assert result["simulated"] is False
        assert result["ok"] is True
        assert result["matched"] is True
        assert "async def run_tool" in result["stdout"]
        return {
            "role": "assistant",
            "content": "Search finished.",
            "tool_calls": [],
        }

    monkeypatch.setattr(
        "automata_api.services.agent.create_llm_response",
        fake_create_llm_response,
    )

    with client.websocket_connect("/ws/chat") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "prompt",
                "session_id": session["id"],
                "prompt": "search for run_tool",
            }
        )

        events = []
        while True:
            event = websocket.receive_json()
            events.append(event)
            if event["type"] in {"done", "error"}:
                break

    assert [event["type"] for event in events] == [
        "started",
        "agent_step",
        "tool_call",
        "tool_result",
        "agent_step",
        "token",
        "done",
    ]
    assert events[2]["tool"] == "rg"
    assert events[3]["success"] is True
    assert '"simulated": false' in events[3]["content"]
    assert events[5]["content"] == "Search finished."
    assert len(calls) == 2
