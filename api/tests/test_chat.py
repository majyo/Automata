import json

import pytest

from automata_api.repositories.sessions import fetch_plan
from automata_api.repositories.sessions import get_context_messages_after_sequence
from automata_api.agent import tools


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


def assert_tool_run_message(
    message,
    *,
    tool_call_id,
    tool,
    arguments_contains,
    result_contains,
    success=True,
):
    assert message["role"] == "tool"
    assert message["kind"] == "tool_run"
    assert message["content"] == ""
    metadata = message["metadata"]
    assert metadata["tool_call_id"] == tool_call_id
    assert metadata["tool"] == tool
    assert arguments_contains in metadata["arguments"]
    assert metadata["result"]["success"] is success
    assert result_contains in metadata["result"]["content"]


def stream_from_completion(create_response):
    async def fake_stream_chat_completion(messages, tools=None):
        response = await create_response(messages, tools)
        delta = {}
        content = response.get("content")
        if isinstance(content, str) and content:
            delta["content"] = content

        reasoning_content = response.get("reasoning_content")
        if isinstance(reasoning_content, str):
            delta["reasoning_content"] = reasoning_content

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


def test_chat_websocket_runs_agent_loop_with_read_file_tool(client, monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    default_workspace = tmp_path / "default"
    session_workspace = tmp_path / "session"
    default_workspace.mkdir()
    session_workspace.mkdir()
    monkeypatch.setenv("AUTOMATA_WORKSPACE_DIR", str(default_workspace))
    (default_workspace / "README.md").write_text("default details\n", encoding="utf-8")
    (session_workspace / "README.md").write_text("workspace details\n", encoding="utf-8")
    session = client.post(
        "/sessions",
        json={"title": "Agent", "working_directory": str(session_workspace)},
    ).json()
    calls = []

    async def fake_create_llm_response(messages, tools=None):
        calls.append({"messages": list(messages), "tools": tools})
        if len(calls) == 1:
            assert tools is not None
            assert any(
                tool["function"]["name"] == "read_file" for tool in tools
            )
            return {
                "role": "assistant",
                "content": "",
                "reasoning_content": "Need to read the workspace file.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "README.md"}',
                        },
                    }
                ],
            }

        assert messages[-2]["role"] == "assistant"
        assert messages[-2]["reasoning_content"] == "Need to read the workspace file."
        assert messages[-1]["role"] == "tool"
        assert messages[-1]["tool_call_id"] == "call_1"
        assert '"simulated": false' in messages[-1]["content"]
        assert "workspace details" in messages[-1]["content"]
        return {
            "role": "assistant",
            "content": "I read the workspace file and finished.",
            "tool_calls": [],
        }

    monkeypatch.setattr(
        "automata_api.agent.llm.stream_chat_completion",
        stream_from_completion(fake_create_llm_response),
    )

    with client.websocket_connect("/ws/chat") as websocket:
        ready = websocket.receive_json()
        websocket.send_json(
            {
                "type": "prompt",
                "session_id": session["id"],
                "prompt": "read the workspace README",
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
    assert events[2]["tool"] == "read_file"
    assert events[2]["tool_call_id"] == "call_1"
    assert events[3]["tool_call_id"] == "call_1"
    assert events[3]["success"] is True
    assert '"simulated": false' in events[3]["content"]
    assert "workspace details" in events[3]["content"]
    assert token_content(events) == "I read the workspace file and finished."

    messages = client.get(f"/sessions/{session['id']}/messages").json()
    assert [message["role"] for message in messages] == [
        "user",
        "tool",
        "agent",
    ]
    assert_tool_run_message(
        messages[1],
        tool_call_id="call_1",
        tool="read_file",
        arguments_contains="README.md",
        result_contains="workspace details",
    )
    assert messages[2]["content"] == "I read the workspace file and finished."
    assert len(calls) == 2


def test_chat_websocket_restores_persisted_tool_protocol_context(
    client, monkeypatch, tmp_path
):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    monkeypatch.setenv("AUTOMATA_WORKSPACE_DIR", str(tmp_path))
    (tmp_path / "README.md").write_text("readme details\n", encoding="utf-8")
    (tmp_path / "NOTES.md").write_text("notes details\n", encoding="utf-8")
    session = client.post("/sessions", json={"title": "Restored Agent"}).json()
    calls = []

    async def fake_create_llm_response(messages, tools=None):
        calls.append({"messages": list(messages), "tools": tools})
        if len(calls) == 1:
            assert [message["role"] for message in messages] == ["system", "user"]
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_readme",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "README.md"}',
                        },
                    },
                    {
                        "id": "call_notes",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "NOTES.md"}',
                        },
                    },
                ],
            }

        if len(calls) == 2:
            assert messages[-3]["role"] == "assistant"
            assert len(messages[-3]["tool_calls"]) == 2
            assert messages[-2]["role"] == "tool"
            assert messages[-2]["tool_call_id"] == "call_readme"
            assert messages[-1]["role"] == "tool"
            assert messages[-1]["tool_call_id"] == "call_notes"
            return {
                "role": "assistant",
                "content": "First run done.",
                "tool_calls": [],
            }

        assert [message["role"] for message in messages] == [
            "system",
            "user",
            "assistant",
            "tool",
            "tool",
            "assistant",
            "user",
        ]
        restored_tool_call = messages[2]
        assert restored_tool_call["content"] is None
        assert [call["id"] for call in restored_tool_call["tool_calls"]] == [
            "call_readme",
            "call_notes",
        ]
        assert messages[3]["tool_call_id"] == "call_readme"
        assert "readme details" in messages[3]["content"]
        assert messages[4]["tool_call_id"] == "call_notes"
        assert "notes details" in messages[4]["content"]
        assert messages[5] == {"role": "assistant", "content": "First run done."}
        assert messages[6] == {"role": "user", "content": "continue with context"}
        assert not any(
            isinstance(message.get("content"), str)
            and message["content"].startswith("Tool call:")
            for message in messages
        )
        return {
            "role": "assistant",
            "content": "Restored context ok.",
            "tool_calls": [],
        }

    monkeypatch.setattr(
        "automata_api.agent.llm.stream_chat_completion",
        stream_from_completion(fake_create_llm_response),
    )

    with client.websocket_connect("/ws/chat") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "prompt",
                "session_id": session["id"],
                "prompt": "read both files",
            }
        )
        first_events = []
        while True:
            event = websocket.receive_json()
            first_events.append(event)
            if event["type"] in {"done", "error"}:
                break

        websocket.send_json(
            {
                "type": "prompt",
                "session_id": session["id"],
                "prompt": "continue with context",
            }
        )
        second_events = []
        while True:
            event = websocket.receive_json()
            second_events.append(event)
            if event["type"] in {"done", "error"}:
                break

    assert compact_event_types(first_events) == [
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
    assert compact_event_types(second_events) == [
        "started",
        "agent_step",
        "token",
        "done",
    ]
    assert token_content(first_events) == "First run done."
    assert token_content(second_events) == "Restored context ok."

    visible_messages = client.get(f"/sessions/{session['id']}/messages").json()
    assert [message["role"] for message in visible_messages] == [
        "user",
        "tool",
        "tool",
        "agent",
        "user",
        "agent",
    ]
    assert_tool_run_message(
        visible_messages[1],
        tool_call_id="call_readme",
        tool="read_file",
        arguments_contains="README.md",
        result_contains="readme details",
    )
    assert_tool_run_message(
        visible_messages[2],
        tool_call_id="call_notes",
        tool="read_file",
        arguments_contains="NOTES.md",
        result_contains="notes details",
    )
    context_messages = get_context_messages_after_sequence(session["id"], 0)
    assert [row["message"]["role"] for row in context_messages] == [
        "user",
        "assistant",
        "tool",
        "tool",
        "assistant",
        "user",
        "assistant",
    ]
    assert len(context_messages[1]["message"]["tool_calls"]) == 2
    assert context_messages[2]["message"]["tool_call_id"] == "call_readme"
    assert context_messages[3]["message"]["tool_call_id"] == "call_notes"


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
        "automata_api.agent.llm.stream_chat_completion",
        stream_from_completion(fake_create_llm_response),
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

    messages = client.get(f"/sessions/{session['id']}/messages").json()
    assert [message["role"] for message in messages] == [
        "user",
        "tool",
        "agent",
    ]
    assert_tool_run_message(
        messages[1],
        tool_call_id="call_bash_1",
        tool="run_bash",
        arguments_contains="printf agent-bash",
        result_contains='"stdout": "agent-bash"',
    )
    assert messages[2]["content"] == "Bash finished."
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
                                '"path": "api/automata_api/agent/tools/__init__.py"}'
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
        "automata_api.agent.llm.stream_chat_completion",
        stream_from_completion(fake_create_llm_response),
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

    messages = client.get(f"/sessions/{session['id']}/messages").json()
    assert [message["role"] for message in messages] == [
        "user",
        "tool",
        "agent",
    ]
    assert_tool_run_message(
        messages[1],
        tool_call_id="call_rg_1",
        tool="rg",
        arguments_contains="async def run_tool",
        result_contains='"matched": true',
    )
    assert messages[2]["content"] == "Search finished."
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
        "automata_api.agent.llm.stream_chat_completion",
        stream_from_completion(fake_create_llm_response),
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

    messages = client.get(f"/sessions/{session['id']}/messages").json()
    assert [message["role"] for message in messages] == [
        "user",
        "tool",
        "tool",
        "agent",
    ]
    assert_tool_run_message(
        messages[1],
        tool_call_id="call_write_1",
        tool="write_file",
        arguments_contains=".build/test-file-tool.txt",
        result_contains='"ok": true',
    )
    assert_tool_run_message(
        messages[2],
        tool_call_id="call_read_1",
        tool="read_file",
        arguments_contains=".build/test-file-tool.txt",
        result_contains="file-tool-ok",
    )
    assert messages[3]["content"] == "File tools finished."
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
        "automata_api.agent.llm.stream_chat_completion",
        stream_from_completion(fake_create_llm_response),
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

    messages = client.get(f"/sessions/{session['id']}/messages").json()
    assert [message["role"] for message in messages] == [
        "user",
        "tool",
        "agent",
    ]
    assert_tool_run_message(
        messages[1],
        tool_call_id="call_patch_1",
        tool="apply_patch",
        arguments_contains='"dry_run": false',
        result_contains='"dry_run": false',
    )
    assert messages[2]["content"] == "Patch finished."
    assert len(calls) == 2


def test_chat_websocket_plan_mode_persists_pending_plan(client, monkeypatch):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    session = client.post("/sessions", json={"title": "Plan Agent"}).json()
    calls = []

    async def fake_create_llm_response(messages, tools=None):
        calls.append({"messages": list(messages), "tools": tools})
        assert tools is not None
        tool_names = {tool["function"]["name"] for tool in tools}
        assert "read_file" in tool_names
        assert "apply_patch_preview" in tool_names
        assert "run_bash" not in tool_names
        assert "write_file" not in tool_names
        assert "apply_patch" not in tool_names
        assert "backend Plan mode" in messages[0]["content"]
        return {
            "role": "assistant",
            "content": "# Plan\n\n1. Inspect.\n2. Implement.",
            "tool_calls": [],
        }

    monkeypatch.setattr(
        "automata_api.agent.llm.stream_chat_completion",
        stream_from_completion(fake_create_llm_response),
    )

    with client.websocket_connect("/ws/chat") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "prompt",
                "session_id": session["id"],
                "prompt": "make a plan",
                "mode": "plan",
            }
        )

        events = []
        while True:
            event = websocket.receive_json()
            events.append(event)
            if event["type"] == "done":
                break

    assert compact_event_types(events) == [
        "started",
        "agent_step",
        "token",
        "plan_ready",
        "done",
    ]
    assert token_content(events) == "# Plan\n\n1. Inspect.\n2. Implement."
    plan_ready = next(event for event in events if event["type"] == "plan_ready")
    assert plan_ready["status"] == "pending"
    assert plan_ready["content"] == "# Plan\n\n1. Inspect.\n2. Implement."

    plan = fetch_plan(session["id"], plan_ready["plan_id"])
    assert plan["status"] == "pending"
    assert plan["content"] == plan_ready["content"]

    messages = client.get(f"/sessions/{session['id']}/messages").json()
    assert [message["role"] for message in messages] == ["user", "agent"]
    assert messages[1]["content"] == plan_ready["content"]
    assert len(calls) == 1


def test_chat_websocket_plan_mode_blocks_mutating_tools(client, monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    monkeypatch.setenv("AUTOMATA_WORKSPACE_DIR", str(tmp_path))
    session = client.post("/sessions", json={"title": "Blocked Plan"}).json()
    target = tmp_path / "blocked.txt"
    calls = []

    async def fake_create_llm_response(messages, tools=None):
        calls.append({"messages": list(messages), "tools": tools})
        if len(calls) == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_write_blocked",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": (
                                '{"path": "blocked.txt", "content": "should not write"}'
                            ),
                        },
                    }
                ],
            }

        assert messages[-1]["role"] == "tool"
        result = json.loads(messages[-1]["content"])
        assert result["ok"] is False
        assert result["error"] == "blocked_by_plan_mode"
        return {
            "role": "assistant",
            "content": "Plan after blocked tool.",
            "tool_calls": [],
        }

    monkeypatch.setattr(
        "automata_api.agent.llm.stream_chat_completion",
        stream_from_completion(fake_create_llm_response),
    )

    with client.websocket_connect("/ws/chat") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "prompt",
                "session_id": session["id"],
                "prompt": "plan with a bad tool",
                "mode": "plan",
            }
        )

        events = []
        while True:
            event = websocket.receive_json()
            events.append(event)
            if event["type"] == "done":
                break

    assert compact_event_types(events) == [
        "started",
        "agent_step",
        "tool_call",
        "tool_result",
        "agent_step",
        "token",
        "plan_ready",
        "done",
    ]
    assert events[2]["tool"] == "write_file"
    assert events[3]["success"] is False
    assert "blocked_by_plan_mode" in events[3]["content"]
    assert not target.exists()

    messages = client.get(f"/sessions/{session['id']}/messages").json()
    assert [message["role"] for message in messages] == [
        "user",
        "tool",
        "agent",
    ]
    assert_tool_run_message(
        messages[1],
        tool_call_id="call_write_blocked",
        tool="write_file",
        arguments_contains="blocked.txt",
        result_contains="blocked_by_plan_mode",
        success=False,
    )
    assert messages[2]["content"] == "Plan after blocked tool."


def test_chat_websocket_approve_plan_executes_and_marks_executed(client, monkeypatch):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    session = client.post("/sessions", json={"title": "Approve Plan"}).json()
    calls = []

    async def fake_create_llm_response(messages, tools=None):
        calls.append({"messages": list(messages), "tools": tools})
        if len(calls) == 1:
            return {
                "role": "assistant",
                "content": "Approved implementation plan.",
                "tool_calls": [],
            }

        assert tools is not None
        assert any(tool["function"]["name"] == "write_file" for tool in tools)
        assert any(
            "Approved implementation plan." in message["content"]
            for message in messages
            if message["role"] == "system"
        )
        return {
            "role": "assistant",
            "content": "Executed approved plan.",
            "tool_calls": [],
        }

    monkeypatch.setattr(
        "automata_api.agent.llm.stream_chat_completion",
        stream_from_completion(fake_create_llm_response),
    )

    with client.websocket_connect("/ws/chat") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "prompt",
                "session_id": session["id"],
                "prompt": "make a plan",
                "mode": "plan",
            }
        )

        plan_events = []
        while True:
            event = websocket.receive_json()
            plan_events.append(event)
            if event["type"] == "done":
                break

        plan_ready = next(event for event in plan_events if event["type"] == "plan_ready")
        plan_id = plan_ready["plan_id"]
        websocket.send_json(
            {
                "type": "approve_plan",
                "session_id": session["id"],
                "plan_id": plan_id,
            }
        )

        approve_events = []
        while True:
            event = websocket.receive_json()
            approve_events.append(event)
            if event["type"] == "done":
                break

        websocket.send_json(
            {
                "type": "approve_plan",
                "session_id": session["id"],
                "plan_id": plan_id,
            }
        )
        repeated_approval = websocket.receive_json()

    assert compact_event_types(approve_events) == [
        "plan_approved",
        "started",
        "agent_step",
        "token",
        "done",
    ]
    assert approve_events[0]["plan_id"] == plan_id
    assert token_content(approve_events) == "Executed approved plan."
    assert fetch_plan(session["id"], plan_id)["status"] == "executed"

    messages = client.get(f"/sessions/{session['id']}/messages").json()
    assert [message["role"] for message in messages] == ["user", "agent", "agent"]
    assert messages[1]["content"] == "Approved implementation plan."
    assert messages[2]["content"] == "Executed approved plan."
    assert len(calls) == 2
    assert repeated_approval["type"] == "plan_error"
    assert "Plan is not pending: executed" in repeated_approval["message"]


def test_chat_websocket_approve_plan_rejects_invalid_plan(client):
    session = client.post("/sessions", json={"title": "Bad Approval"}).json()

    with client.websocket_connect("/ws/chat") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "approve_plan",
                "session_id": session["id"],
                "plan_id": "missing-plan",
            }
        )
        event = websocket.receive_json()

    assert event == {"type": "plan_error", "message": "Plan not found"}
