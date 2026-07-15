import asyncio
import json


async def waiting_model(*_args, **_kwargs):
    await asyncio.Event().wait()
    if False:
        yield {}


async def write_tool_model(*_args, **_kwargs):
    yield {
        "tool_calls": [
            {
                "index": 0,
                "id": "call-write",
                "type": "function",
                "function": {
                    "name": "write_file",
                    "arguments": json.dumps(
                        {"path": "cancelled.txt", "content": "must not exist"}
                    ),
                },
            }
        ]
    }


def test_same_session_rejects_concurrent_run_and_cancel_stops_model_wait(
    client, monkeypatch
):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    monkeypatch.setattr(
        "automata_api.agent.llm.stream_chat_completion", waiting_model
    )
    session = client.post("/sessions", json={"title": "Busy"}).json()
    other_session = client.post("/sessions", json={"title": "Also busy"}).json()

    with client.websocket_connect("/ws/chat") as first:
        first.receive_json()
        first.send_json(
            {"type": "prompt", "session_id": session["id"], "prompt": "wait"}
        )
        started = first.receive_json()
        assert started["type"] == "started"
        assert first.receive_json()["type"] == "agent_step"

        first.send_json(
            {
                "type": "prompt",
                "session_id": other_session["id"],
                "prompt": "same connection race",
            }
        )
        connection_busy = first.receive_json()
        assert connection_busy["type"] == "run_error"
        assert connection_busy["code"] == "connection_busy"
        assert connection_busy["run_id"] == started["run_id"]

        with client.websocket_connect("/ws/chat") as second:
            second.receive_json()
            second.send_json(
                {"type": "prompt", "session_id": session["id"], "prompt": "race"}
            )
            busy = second.receive_json()
            assert busy["type"] == "run_error"
            assert busy["code"] == "session_busy"
            assert busy["run_id"] == started["run_id"]

        first.send_json({"type": "cancel_run", "run_id": started["run_id"]})
        assert first.receive_json()["type"] == "run_cancel_requested"
        cancelled = first.receive_json()
        assert cancelled["type"] == "run_cancelled"
        assert cancelled["run_id"] == started["run_id"]


def test_cancel_while_waiting_for_approval_prevents_tool_execution(
    client, monkeypatch, tmp_path
):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    monkeypatch.setattr(
        "automata_api.agent.llm.stream_chat_completion", write_tool_model
    )
    session = client.post(
        "/sessions",
        json={"title": "Approval cancel", "working_directory": str(tmp_path)},
    ).json()

    with client.websocket_connect("/ws/chat") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "prompt",
                "session_id": session["id"],
                "prompt": "write a file",
            }
        )
        approval = None
        while approval is None:
            event = websocket.receive_json()
            if event["type"] == "tool_approval_required":
                approval = event

        websocket.send_json(
            {"type": "cancel_run", "run_id": approval["run_id"]}
        )
        assert websocket.receive_json()["type"] == "run_cancel_requested"
        assert websocket.receive_json()["type"] == "run_cancelled"

    assert not (tmp_path / "cancelled.txt").exists()


def test_disconnect_releases_session_lease(client, monkeypatch):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    monkeypatch.setattr(
        "automata_api.agent.llm.stream_chat_completion", waiting_model
    )
    session = client.post("/sessions", json={"title": "Disconnect"}).json()

    with client.websocket_connect("/ws/chat") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {"type": "prompt", "session_id": session["id"], "prompt": "wait"}
        )
        assert websocket.receive_json()["type"] == "started"
        assert websocket.receive_json()["type"] == "agent_step"

    with client.websocket_connect("/ws/chat") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {"type": "prompt", "session_id": session["id"], "prompt": "again"}
        )
        started = websocket.receive_json()
        assert started["type"] == "started"
        websocket.send_json({"type": "cancel_run", "run_id": started["run_id"]})
        assert websocket.receive_json()["type"] == "run_cancel_requested"
        assert websocket.receive_json()["type"] == "run_cancelled"


def test_plan_mode_write_is_denied_without_approval_even_if_client_tries_to_allow(
    client, monkeypatch, tmp_path
):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    calls = 0

    async def plan_model(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            async for event in write_tool_model():
                yield event
            return
        yield {"content": "Write was correctly blocked."}

    monkeypatch.setattr(
        "automata_api.agent.llm.stream_chat_completion", plan_model
    )
    session = client.post(
        "/sessions",
        json={"title": "Plan isolation", "working_directory": str(tmp_path)},
    ).json()

    with client.websocket_connect("/ws/chat") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {
                "type": "prompt",
                "session_id": session["id"],
                "prompt": "plan a write",
                "mode": "plan",
            }
        )
        events = []
        while True:
            event = websocket.receive_json()
            events.append(event)
            if event["type"] == "done":
                break

        run_id = next(event["run_id"] for event in events if event["type"] == "started")
        websocket.send_json(
            {
                "type": "tool_approval_response",
                "run_id": run_id,
                "approval_id": "fabricated",
                "decision": "allow_once",
            }
        )
        rejected = websocket.receive_json()

    assert all(event["type"] != "tool_approval_required" for event in events)
    tool_result = next(event for event in events if event["type"] == "tool_result")
    assert json.loads(tool_result["content"])["error"] == "blocked_by_plan_mode"
    assert rejected["type"] == "approval_error"
    assert rejected["code"] in {"run_not_found", "approval_not_found"}
    assert not (tmp_path / "cancelled.txt").exists()
