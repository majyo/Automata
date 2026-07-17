import asyncio
import sqlite3

import pytest

from automata_api.db.connection import db_path
from automata_api.agent.execution.events import DurableRunEventSink
from automata_api.repositories import runs
from automata_api.repositories.sessions import create_plan, save_message


def test_run_repository_persists_state_and_ordered_events(client):
    session = client.post("/sessions", json={"title": "Run repository"}).json()
    run, prompt = runs.create_prompt_run(
        session_id=session["id"],
        prompt="hello",
        mode="act",
        owner_instance_id="instance-a",
    )

    assert run["status"] == "queued"
    assert run["request_message_id"] == prompt["id"]
    assert client.get(f"/sessions/{session['id']}/messages").json()[0]["content"] == "hello"

    runs.transition_run(
        run["id"],
        expected=("queued",),
        target="running",
    )
    first = runs.append_event(run["id"], {"type": "started", "prompt": "hello"})
    second = runs.append_event(run["id"], {"type": "token", "content": "ok"})
    terminal = runs.finish_run(
        run["id"],
        status="completed",
        event={"type": "done", "message": None},
    )

    assert [first["seq"], second["seq"], terminal["seq"]] == [1, 2, 3]
    assert [event["type"] for event in runs.list_events(run["id"])] == [
        "started",
        "token",
        "done",
    ]
    assert runs.get_run(run["id"])["status"] == "completed"


def test_final_message_and_run_terminal_state_commit_atomically(
    client, monkeypatch
):
    session = client.post("/sessions", json={"title": "Atomic finish"}).json()
    run = runs.create_run(
        session_id=session["id"],
        kind="chat_act",
        mode="act",
        owner_instance_id="instance-a",
    )
    runs.transition_run(run["id"], expected=("queued",), target="running")

    def fail_encoding(*_args, **_kwargs):
        raise RuntimeError("simulated event encoding failure")

    monkeypatch.setattr(runs, "encode_event", fail_encoding)
    with pytest.raises(RuntimeError, match="simulated"):
        runs.finish_run(
            run["id"],
            status="completed",
            event={"type": "done"},
            response_content="must roll back",
        )

    assert runs.get_run(run["id"])["status"] == "running"
    assert client.get(f"/sessions/{session['id']}/messages").json() == []


def test_terminal_event_retention_keeps_summary_and_invalidates_old_cursor(client):
    session = client.post("/sessions", json={"title": "Retention"}).json()
    run = runs.create_run(
        session_id=session["id"],
        kind="chat_act",
        mode="act",
        owner_instance_id="instance-a",
    )
    runs.transition_run(run["id"], expected=("queued",), target="running")
    runs.append_event(run["id"], {"type": "started"})
    runs.append_event(run["id"], {"type": "token", "content": "old"})
    terminal = runs.finish_run(
        run["id"],
        status="completed",
        event={"type": "done"},
        response_content="completed",
    )
    with sqlite3.connect(db_path()) as db:
        db.execute(
            """
            UPDATE agent_run_events
            SET created_at = '2000-01-01T00:00:00+00:00'
            WHERE run_id = ?
            """,
            (run["id"],),
        )

    assert runs.prune_terminal_run_events(30) == 2
    assert runs.list_events(
        run["id"], after_sequence=terminal["seq"] - 1
    )[0]["type"] == "done"
    with pytest.raises(runs.EventCursorError):
        runs.list_events(run["id"], after_sequence=0)

    response = client.get(
        f"/sessions/{session['id']}/runs/{run['id']}/events",
        params={"after_sequence": 0},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "event_cursor_invalid"


def test_database_enforces_one_non_terminal_run_per_session(client):
    session = client.post("/sessions", json={"title": "Single run"}).json()
    first = runs.create_run(
        session_id=session["id"],
        kind="chat_act",
        mode="act",
        owner_instance_id="instance-a",
    )

    with pytest.raises(runs.SessionBusyError) as captured:
        runs.create_run(
            session_id=session["id"],
            kind="chat_plan",
            mode="plan",
            owner_instance_id="instance-a",
        )

    assert captured.value.run_id == first["id"]


def test_different_sessions_can_have_active_runs(client):
    first_session = client.post("/sessions", json={"title": "A"}).json()
    second_session = client.post("/sessions", json={"title": "B"}).json()

    first = runs.create_run(
        session_id=first_session["id"],
        kind="chat_act",
        mode="act",
        owner_instance_id="instance-a",
    )
    second = runs.create_run(
        session_id=second_session["id"],
        kind="chat_act",
        mode="act",
        owner_instance_id="instance-a",
    )

    assert first["session_id"] != second["session_id"]
    assert len(runs.list_runs(non_terminal_only=True)) == 2


def test_active_session_cannot_be_deleted_implicitly(client):
    session = client.post("/sessions", json={"title": "Keep running"}).json()
    run = runs.create_run(
        session_id=session["id"],
        kind="chat_act",
        mode="act",
        owner_instance_id="instance-a",
    )

    response = client.delete(f"/sessions/{session['id']}")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "session_busy"
    assert response.json()["detail"]["run_id"] == run["id"]
    assert runs.get_run(run["id"])["status"] == "queued"


def test_startup_marks_old_instance_runs_interrupted(client):
    session = client.post("/sessions", json={"title": "Restart"}).json()
    run = runs.create_run(
        session_id=session["id"],
        kind="chat_act",
        mode="act",
        owner_instance_id="old-instance",
    )
    runs.transition_run(run["id"], expected=("queued",), target="running")

    events = runs.interrupt_stale_runs("new-instance")

    assert len(events) == 1
    assert events[0]["type"] == "run_interrupted"
    assert runs.get_run(run["id"])["status"] == "interrupted"
    assert runs.list_events(run["id"])[0]["code"] == "api_process_restarted"


def test_plan_retry_attempts_are_idempotent_and_use_new_runs(client):
    session = client.post("/sessions", json={"title": "Plan attempts"}).json()
    prompt = save_message(session["id"], "user", "prompt")
    plan_message = save_message(session["id"], "agent", "plan")
    plan = create_plan(
        session_id=session["id"],
        prompt_message_id=prompt["id"],
        plan_message_id=plan_message["id"],
        content="plan",
    )

    first_run, _, idempotent = runs.begin_plan_execution(
        session_id=session["id"],
        plan_id=plan["id"],
        request_id="request-1",
        owner_instance_id="instance-a",
        retry=False,
    )
    assert idempotent is False
    runs.transition_run(first_run["id"], expected=("queued",), target="running")
    runs.finish_run(
        first_run["id"],
        status="failed",
        event={"type": "error", "code": "test_failure", "message": "failed"},
        error_code="test_failure",
        public_error="failed",
    )

    retry_run, _, retry_idempotent = runs.begin_plan_execution(
        session_id=session["id"],
        plan_id=plan["id"],
        request_id="request-2",
        owner_instance_id="instance-a",
        retry=True,
    )
    same_run, _, repeated = runs.begin_plan_execution(
        session_id=session["id"],
        plan_id=plan["id"],
        request_id="request-2",
        owner_instance_id="instance-a",
        retry=True,
    )

    assert retry_idempotent is False
    assert repeated is True
    assert retry_run["id"] == same_run["id"]
    attempts = runs.list_plan_attempts(session["id"], plan["id"])
    assert [attempt["attempt_no"] for attempt in attempts] == [1, 2]
    assert attempts[0]["status"] == "failed"
    assert attempts[1]["status"] == "queued"


def test_run_events_do_not_store_secret_fields(client):
    session = client.post("/sessions", json={"title": "Redaction"}).json()
    run = runs.create_run(
        session_id=session["id"],
        kind="chat_act",
        mode="act",
        owner_instance_id="instance-a",
    )
    asyncio.run(
        DurableRunEventSink(run_id=run["id"]).send_json(
            {
                "type": "test",
                "authorization": "Bearer secret",
            }
        )
    )

    with sqlite3.connect(db_path()) as db:
        stored = db.execute(
            "SELECT payload_json FROM agent_run_events WHERE run_id = ?",
            (run["id"],),
        ).fetchone()[0]
    assert "Bearer secret" not in stored
    assert "authorization" not in stored
