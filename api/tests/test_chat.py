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
