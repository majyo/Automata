import asyncio
import threading


def test_steer_is_applied_before_the_next_model_call(client, monkeypatch):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    release = threading.Event()
    calls = 0

    async def model(messages, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            yield {"content": "draft"}
            while not release.is_set():
                await asyncio.sleep(0.01)
            yield {"content": " initial"}
            return
        assert messages[-1] == {"role": "user", "content": "focus on tests"}
        yield {"content": "steered answer"}

    monkeypatch.setattr(
        "automata_api.infrastructure.llm.client.stream_chat_completion", model
    )
    session = client.post("/sessions", json={"title": "Steer delivery"}).json()

    with client.websocket_connect("/ws/chat") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {"type": "prompt", "session_id": session["id"], "prompt": "start"}
        )
        started = receive_matching(websocket, lambda event: event["type"] == "started")
        receive_matching(
            websocket,
            lambda event: event.get("type") == "agent_step"
            and event.get("run_id") == started["run_id"],
        )
        receive_matching(
            websocket,
            lambda event: event.get("type") == "token"
            and event.get("run_id") == started["run_id"],
        )

        websocket.send_json(
            {
                "type": "prompt",
                "session_id": session["id"],
                "prompt": "focus on tests",
                "delivery": "steer",
                "run_id": started["run_id"],
                "request_id": "steer-request-1",
            }
        )
        accepted = receive_matching(
            websocket, lambda event: event["type"] == "input_accepted"
        )
        assert accepted["delivery"] == "steer"
        assert accepted["status"] == "pending"
        release.set()

        applied = receive_matching(
            websocket,
            lambda event: event.get("type") == "input_applied"
            and event.get("run_id") == started["run_id"],
        )
        assert applied["delivery"] == "steer"
        done = receive_matching(
            websocket,
            lambda event: event.get("type") == "done"
            and event.get("run_id") == started["run_id"],
        )
        assert done["message"]["content"] == "steered answer"

    messages = client.get(f"/sessions/{session['id']}/messages").json()
    assert [message["content"] for message in messages] == [
        "start",
        "draft initial",
        "focus on tests",
        "steered answer",
    ]


def test_queue_materializes_a_successor_run_after_completion(client, monkeypatch):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    release = threading.Event()
    calls = 0

    async def model(_messages, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            while not release.is_set():
                await asyncio.sleep(0.01)
            yield {"content": "first answer"}
            return
        yield {"content": "queued answer"}

    monkeypatch.setattr(
        "automata_api.infrastructure.llm.client.stream_chat_completion", model
    )
    session = client.post("/sessions", json={"title": "Queue delivery"}).json()

    with client.websocket_connect("/ws/chat") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {"type": "prompt", "session_id": session["id"], "prompt": "first"}
        )
        first = receive_matching(websocket, lambda event: event["type"] == "started")
        receive_matching(
            websocket,
            lambda event: event.get("type") == "agent_step"
            and event.get("run_id") == first["run_id"],
        )

        websocket.send_json(
            {
                "type": "prompt",
                "session_id": session["id"],
                "prompt": "second",
                "delivery": "queue",
                "request_id": "queue-request-1",
            }
        )
        accepted = receive_matching(
            websocket, lambda event: event["type"] == "input_accepted"
        )
        assert accepted["delivery"] == "queue"
        assert accepted["status"] == "pending"

        release.set()
        first_done = receive_matching(
            websocket,
            lambda event: event.get("type") == "done"
            and event.get("run_id") == first["run_id"],
        )
        assert first_done["message"]["content"] == "first answer"
        second = receive_matching(
            websocket,
            lambda event: event.get("type") == "started"
            and event.get("run_id") != first["run_id"],
        )
        second_done = receive_matching(
            websocket,
            lambda event: event.get("type") == "done"
            and event.get("run_id") == second["run_id"],
        )
        assert second_done["message"]["content"] == "queued answer"

    messages = client.get(f"/sessions/{session['id']}/messages").json()
    assert [message["content"] for message in messages] == [
        "first",
        "first answer",
        "second",
        "queued answer",
    ]


def receive_matching(websocket, predicate):
    while True:
        event = websocket.receive_json()
        if predicate(event):
            return event


def test_cancelled_queue_item_never_materializes(client, monkeypatch):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    release = threading.Event()

    async def model(_messages, **_kwargs):
        while not release.is_set():
            await asyncio.sleep(0.01)
        yield {"content": "first answer"}

    monkeypatch.setattr(
        "automata_api.infrastructure.llm.client.stream_chat_completion", model
    )
    session = client.post("/sessions", json={"title": "Cancel queue"}).json()

    with client.websocket_connect("/ws/chat") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {"type": "prompt", "session_id": session["id"], "prompt": "first"}
        )
        first = receive_matching(websocket, lambda event: event["type"] == "started")
        receive_matching(
            websocket,
            lambda event: event.get("type") == "agent_step"
            and event.get("run_id") == first["run_id"],
        )

        websocket.send_json(
            {
                "type": "prompt",
                "session_id": session["id"],
                "prompt": "second",
                "delivery": "queue",
                "request_id": "cancel-queue-1",
            }
        )
        accepted = receive_matching(
            websocket, lambda event: event["type"] == "input_accepted"
        )

        websocket.send_json(
            {
                "type": "cancel_input",
                "session_id": session["id"],
                "input_id": accepted["input_id"],
            }
        )
        cancelled = receive_matching(
            websocket, lambda event: event["type"] == "input_cancelled"
        )
        assert cancelled["input_id"] == accepted["input_id"]

        release.set()
        done = receive_matching(
            websocket,
            lambda event: event.get("type") == "done"
            and event.get("run_id") == first["run_id"],
        )
        assert done["message"]["content"] == "first answer"

    messages = client.get(f"/sessions/{session['id']}/messages").json()
    assert [message["content"] for message in messages] == ["first", "first answer"]


def test_cancelling_the_same_input_twice_is_idempotent(client, monkeypatch):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    release = threading.Event()

    async def model(_messages, **_kwargs):
        while not release.is_set():
            await asyncio.sleep(0.01)
        yield {"content": "answer"}

    monkeypatch.setattr(
        "automata_api.infrastructure.llm.client.stream_chat_completion", model
    )
    session = client.post("/sessions", json={"title": "Idempotent cancel"}).json()

    with client.websocket_connect("/ws/chat") as websocket:
        websocket.receive_json()
        websocket.send_json(
            {"type": "prompt", "session_id": session["id"], "prompt": "first"}
        )
        first = receive_matching(websocket, lambda event: event["type"] == "started")
        receive_matching(
            websocket,
            lambda event: event.get("type") == "agent_step"
            and event.get("run_id") == first["run_id"],
        )

        websocket.send_json(
            {
                "type": "prompt",
                "session_id": session["id"],
                "prompt": "second",
                "delivery": "queue",
                "request_id": "cancel-queue-2",
            }
        )
        accepted = receive_matching(
            websocket, lambda event: event["type"] == "input_accepted"
        )

        for _ in range(2):
            websocket.send_json(
                {
                    "type": "cancel_input",
                    "session_id": session["id"],
                    "input_id": accepted["input_id"],
                }
            )
            cancelled = receive_matching(
                websocket, lambda event: event["type"] == "input_cancelled"
            )
            assert cancelled["input_id"] == accepted["input_id"]

        websocket.send_json(
            {
                "type": "cancel_input",
                "session_id": session["id"],
                "input_id": "missing-input",
            }
        )
        error = receive_matching(
            websocket, lambda event: event["type"] == "run_error"
        )
        assert error["code"] == "input_not_found"

        release.set()
        receive_matching(
            websocket,
            lambda event: event.get("type") == "done"
            and event.get("run_id") == first["run_id"],
        )

    messages = client.get(f"/sessions/{session['id']}/messages").json()
    assert [message["content"] for message in messages] == ["first", "answer"]
