import asyncio
import json
import time


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


def test_different_sessions_run_concurrently_and_same_session_is_rejected(
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
                "prompt": "other session",
            }
        )
        other_started = receive_matching(
            first,
            lambda event: event.get("type") == "started"
            and event.get("session_id") == other_session["id"],
        )
        assert other_started["run_id"] != started["run_id"]

        with client.websocket_connect("/ws/chat") as second:
            ready = second.receive_json()
            assert {
                run["id"] for run in ready["active_runs"]
            } == {started["run_id"], other_started["run_id"]}
            second.send_json(
                {"type": "prompt", "session_id": session["id"], "prompt": "race"}
            )
            busy = second.receive_json()
            assert busy["type"] == "run_error"
            assert busy["code"] == "session_busy"
            assert busy["run_id"] == started["run_id"]

            second.send_json(
                {
                    "type": "cancel_run",
                    "session_id": session["id"],
                    "run_id": started["run_id"],
                }
            )
            assert receive_run_event(
                second, started["run_id"], "run_cancel_requested"
            )
            cancelled = receive_run_event(
                second, started["run_id"], "run_cancelled"
            )
        assert cancelled["type"] == "run_cancelled"
        assert cancelled["run_id"] == started["run_id"]

        first.send_json(
            {
                "type": "cancel_run",
                "session_id": other_session["id"],
                "run_id": other_started["run_id"],
            }
        )
        assert receive_run_event(
            first, other_started["run_id"], "run_cancel_requested"
        )
        assert receive_run_event(
            first, other_started["run_id"], "run_cancelled"
        )


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


def test_full_access_run_executes_write_without_approval(
    client, monkeypatch, tmp_path
):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    calls = 0

    async def model(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            async for event in write_tool_model():
                yield event
            return
        yield {"content": "Write completed."}

    monkeypatch.setattr(
        "automata_api.agent.llm.stream_chat_completion",
        model,
    )
    session = client.post(
        "/sessions",
        json={
            "title": "Full access",
            "working_directory": str(tmp_path),
            "permission_preset": "full_access",
        },
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
        events = []
        while True:
            event = websocket.receive_json()
            events.append(event)
            if event["type"] == "done":
                break

    started = next(event for event in events if event["type"] == "started")
    assert started["permission_preset"] == "full_access"
    assert all(event["type"] != "tool_approval_required" for event in events)
    assert (tmp_path / "cancelled.txt").read_text(encoding="utf-8") == (
        "must not exist"
    )
    run = client.get(
        f"/sessions/{session['id']}/runs/{started['run_id']}"
    ).json()
    assert run["permission_preset"] == "full_access"


def test_disconnect_keeps_run_in_background_until_explicit_cancel(
    client, monkeypatch
):
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
        started = websocket.receive_json()
        assert started["type"] == "started"
        assert websocket.receive_json()["type"] == "agent_step"

    with client.websocket_connect("/ws/chat") as websocket:
        ready = websocket.receive_json()
        assert [run["id"] for run in ready["active_runs"]] == [
            started["run_id"]
        ]
        websocket.send_json(
            {"type": "prompt", "session_id": session["id"], "prompt": "again"}
        )
        busy = websocket.receive_json()
        assert busy["type"] == "run_error"
        assert busy["code"] == "session_busy"
        assert busy["run_id"] == started["run_id"]

        websocket.send_json(
            {
                "type": "resume_run",
                "session_id": session["id"],
                "run_id": started["run_id"],
                "after_sequence": 0,
            }
        )
        assert websocket.receive_json()["type"] == "run_resume_started"
        assert websocket.receive_json()["type"] == "started"
        assert websocket.receive_json()["type"] == "agent_step"
        assert websocket.receive_json()["type"] == "run_resume_complete"

        websocket.send_json(
            {
                "type": "cancel_run",
                "session_id": session["id"],
                "run_id": started["run_id"],
            }
        )
        assert websocket.receive_json()["type"] == "run_cancel_requested"
        assert websocket.receive_json()["type"] == "run_cancelled"


def test_run_completes_without_any_frontend_connection(client, monkeypatch):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")

    async def delayed_model(*_args, **_kwargs):
        await asyncio.sleep(0.1)
        yield {"content": "completed in background"}

    monkeypatch.setattr(
        "automata_api.agent.llm.stream_chat_completion", delayed_model
    )
    session = client.post("/sessions", json={"title": "Background"}).json()

    with client.websocket_connect("/ws/chat") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {"type": "prompt", "session_id": session["id"], "prompt": "finish"}
        )
        started = websocket.receive_json()
        assert started["type"] == "started"

    deadline = time.monotonic() + 2
    run = None
    while time.monotonic() < deadline:
        run = client.get(
            f"/sessions/{session['id']}/runs/{started['run_id']}"
        ).json()
        if run["status"] == "completed":
            break
        time.sleep(0.02)

    assert run is not None
    assert run["status"] == "completed"
    messages = client.get(f"/sessions/{session['id']}/messages").json()
    assert messages[-1]["content"] == "completed in background"

    with client.websocket_connect("/ws/chat") as websocket:
        ready = websocket.receive_json()
        assert ready["active_runs"] == []
        websocket.send_json(
            {
                "type": "resume_run",
                "session_id": session["id"],
                "run_id": started["run_id"],
                "after_sequence": 0,
            }
        )
        replayed = []
        while True:
            event = websocket.receive_json()
            replayed.append(event)
            if event["type"] == "run_resume_complete":
                break
    assert any(event["type"] == "done" for event in replayed)
    assert replayed[-1]["status"] == "completed"


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


def test_failed_plan_can_be_retried_with_new_attempt(client, monkeypatch):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    calls = 0

    async def model(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {"content": "Implementation plan."}
            return
        if calls == 2:
            raise RuntimeError("planned failure")
        yield {"content": "Retry completed."}

    monkeypatch.setattr(
        "automata_api.agent.llm.stream_chat_completion",
        model,
    )
    session = client.post("/sessions", json={"title": "Retry plan"}).json()

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
        plan_ready = receive_matching(
            websocket, lambda event: event.get("type") == "plan_ready"
        )
        receive_matching(
            websocket,
            lambda event: event.get("type") == "done"
            and event.get("run_id") == plan_ready["run_id"],
        )

        websocket.send_json(
            {
                "type": "approve_plan",
                "session_id": session["id"],
                "plan_id": plan_ready["plan_id"],
                "request_id": "attempt-1",
            }
        )
        failed = receive_matching(
            websocket,
            lambda event: event.get("type") == "error"
            and event.get("session_id") == session["id"],
        )
        assert failed["code"] == "run_failed"

        messages = client.get(f"/sessions/{session['id']}/messages").json()
        stored_plan = next(
            message for message in messages if message["plan_id"] == plan_ready["plan_id"]
        )
        assert stored_plan["plan_status"] == "failed"

        websocket.send_json(
            {
                "type": "retry_plan",
                "session_id": session["id"],
                "plan_id": plan_ready["plan_id"],
                "request_id": "attempt-2",
                "confirm_possible_duplicate_side_effects": True,
            }
        )
        retried = receive_matching(
            websocket,
            lambda event: event.get("type") == "done"
            and event.get("session_id") == session["id"],
        )
        assert retried["message"]["content"] == "Retry completed."

    attempts = client.get(
        f"/sessions/{session['id']}/plans/{plan_ready['plan_id']}/attempts"
    ).json()
    assert [attempt["attempt_no"] for attempt in attempts] == [1, 2]
    assert [attempt["status"] for attempt in attempts] == ["failed", "completed"]


def receive_matching(websocket, predicate):
    while True:
        event = websocket.receive_json()
        if predicate(event):
            return event


def receive_run_event(websocket, run_id: str, event_type: str):
    return receive_matching(
        websocket,
        lambda event: event.get("run_id") == run_id
        and event.get("type") == event_type,
    )
