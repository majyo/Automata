import json

import pytest

from automata_api.services import tools


def patch_text(*lines):
    return "\n".join(lines) + "\n"


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
    assert compact_event_types(events) == [
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
    assert token_content(events) == "I used a simulated workspace inspection and finished."

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

    assert compact_event_types(events) == [
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
    assert token_content(events) == "Bash finished."
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

    assert compact_event_types(events) == [
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
    assert token_content(events) == "Search finished."
    assert len(calls) == 2


def test_chat_websocket_runs_agent_loop_with_file_tools(client, monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    monkeypatch.setenv("AUTOMATA_WORKSPACE_DIR", str(tmp_path))
    session = client.post("/sessions", json={"title": "File Agent"}).json()
    calls = []

    async def fake_create_llm_response(messages, tools=None):
        calls.append({"messages": list(messages), "tools": tools})
        if len(calls) == 1:
            assert tools is not None
            tool_names = {tool["function"]["name"] for tool in tools}
            assert "read_file" in tool_names
            assert "write_file" in tool_names
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_write_1",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": (
                                '{"path": ".build/test-file-tool.txt", '
                                '"content": "file-tool-ok\\n", "mode": "overwrite"}'
                            ),
                        },
                    },
                    {
                        "id": "call_read_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": ".build/test-file-tool.txt"}',
                        },
                    },
                ],
            }

        assert messages[-2]["role"] == "tool"
        write_result = json.loads(messages[-2]["content"])
        assert write_result["simulated"] is False
        assert write_result["ok"] is True
        assert messages[-1]["role"] == "tool"
        read_result = json.loads(messages[-1]["content"])
        assert read_result["simulated"] is False
        assert read_result["ok"] is True
        assert read_result["content"] == "file-tool-ok\n"
        return {
            "role": "assistant",
            "content": "File tools finished.",
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
                "prompt": "write then read a file",
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
        "tool_call",
        "tool_result",
        "agent_step",
        "token",
        "done",
    ]
    assert events[2]["tool"] == "write_file"
    assert events[3]["success"] is True
    assert events[4]["tool"] == "read_file"
    assert events[5]["success"] is True
    assert token_content(events) == "File tools finished."
    assert len(calls) == 2


def test_chat_websocket_runs_agent_loop_with_apply_patch_tool(client, monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    monkeypatch.setenv("AUTOMATA_WORKSPACE_DIR", str(tmp_path))
    source = tmp_path / "sample.txt"
    source.write_text("one\ntwo\nthree\n", encoding="utf-8")
    session = client.post("/sessions", json={"title": "Patch Agent"}).json()
    patch = patch_text(
        "--- a/sample.txt",
        "+++ b/sample.txt",
        "@@ -1,3 +1,3 @@",
        " one",
        "-two",
        "+TWO",
        " three",
    )
    calls = []

    async def fake_create_llm_response(messages, tools=None):
        calls.append({"messages": list(messages), "tools": tools})
        if len(calls) == 1:
            assert tools is not None
            tool_names = {tool["function"]["name"] for tool in tools}
            assert "apply_patch" in tool_names
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_patch_1",
                        "type": "function",
                        "function": {
                            "name": "apply_patch",
                            "arguments": json.dumps(
                                {"patch": patch, "dry_run": False}
                            ),
                        },
                    }
                ],
            }

        assert messages[-1]["role"] == "tool"
        assert messages[-1]["tool_call_id"] == "call_patch_1"
        result = json.loads(messages[-1]["content"])
        assert result["simulated"] is False
        assert result["ok"] is True
        assert result["tool"] == "apply_patch"
        assert result["dry_run"] is False
        assert source.read_text(encoding="utf-8") == "one\nTWO\nthree\n"
        return {
            "role": "assistant",
            "content": "Patch finished.",
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
                "prompt": "patch a file",
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
        "agent_step",
        "token",
        "done",
    ]
    assert events[2]["tool"] == "apply_patch"
    assert events[3]["success"] is True
    assert '"simulated": false' in events[3]["content"]
    assert token_content(events) == "Patch finished."
    assert len(calls) == 2
