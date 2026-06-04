import sqlite3

from automata_api.db.schema import init_db
from automata_api.repositories.sessions import (
    PlanNotFoundError,
    SessionNotFoundError,
    approve_plan,
    create_plan,
    fetch_context_summary,
    fetch_plan,
    list_messages,
    mark_plan_executed,
    get_context_messages_after_sequence,
    get_recent_context_messages,
    save_message,
    save_context_message,
    save_tool_run_message,
    update_tool_run_result,
    upsert_context_summary,
)


def test_session_crud_and_messages(client):
    assert client.get("/sessions").json() == []

    created = client.post("/sessions", json={"title": "Alpha"}).json()
    assert created["title"] == "Alpha"
    assert created["message_count"] == 0

    sessions = client.get("/sessions").json()
    assert len(sessions) == 1
    assert sessions[0]["id"] == created["id"]
    assert sessions[0]["title"] == "Alpha"
    assert sessions[0]["message_count"] == 0

    updated = client.patch(
        f"/sessions/{created['id']}",
        json={"title": "Beta"},
    ).json()
    assert updated["id"] == created["id"]
    assert updated["title"] == "Beta"

    messages = client.get(f"/sessions/{created['id']}/messages")
    assert messages.status_code == 200
    assert messages.json() == []

    deleted = client.delete(f"/sessions/{created['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/sessions/{created['id']}/messages").status_code == 404


def test_context_summary_is_hidden_from_visible_messages(client):
    session = client.post("/sessions", json={"title": "Compression"}).json()
    message = save_message(
        session_id=session["id"],
        role="user",
        content="visible message",
    )

    stored = upsert_context_summary(
        session_id=session["id"],
        content="hidden summary",
        through_sequence=message["sequence"],
    )
    updated = upsert_context_summary(
        session_id=session["id"],
        content="updated hidden summary",
        through_sequence=message["sequence"] + 1,
    )

    assert stored["created_at"] == updated["created_at"]
    assert fetch_context_summary(session["id"])["content"] == "updated hidden summary"
    assert fetch_context_summary(session["id"])["through_sequence"] == message["sequence"] + 1
    assert [row["content"] for row in list_messages(session["id"])] == [
        "visible message"
    ]
    assert client.get(f"/sessions/{session['id']}/messages").json()[0]["content"] == (
        "visible message"
    )


def test_tool_run_messages_are_visible_structured_and_counted(client):
    session = client.post("/sessions", json={"title": "Tools"}).json()
    user_message = save_message(
        session_id=session["id"],
        role="user",
        content="run a tool",
    )
    tool_message = save_tool_run_message(
        session_id=session["id"],
        tool_call_id="call_read",
        tool="read_file",
        arguments='{"path": "README.md"}',
    )
    update_tool_run_result(
        session_id=session["id"],
        message_id=tool_message["id"],
        success=True,
        content='{"ok": true, "content": "readme"}',
    )

    messages = client.get(f"/sessions/{session['id']}/messages").json()
    sessions = client.get("/sessions").json()

    assert [message["role"] for message in messages] == ["user", "tool"]
    assert messages[0]["id"] == user_message["id"]
    assert messages[1]["id"] == tool_message["id"]
    assert messages[1]["kind"] == "tool_run"
    assert messages[1]["content"] == ""
    assert messages[1]["metadata"]["tool_call_id"] == "call_read"
    assert messages[1]["metadata"]["tool"] == "read_file"
    assert messages[1]["metadata"]["arguments"] == '{"path": "README.md"}'
    assert messages[1]["metadata"]["result"]["content"] == '{"ok": true, "content": "readme"}'
    assert sessions[0]["message_count"] == 2


def test_agent_context_messages_are_structured_and_hidden_from_visible_messages(client):
    session = client.post("/sessions", json={"title": "Context"}).json()
    save_message(session_id=session["id"], role="user", content="visible prompt")
    saved = save_context_message(
        session_id=session["id"],
        message={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_read",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
    )

    visible_messages = client.get(f"/sessions/{session['id']}/messages").json()
    recent_context = get_recent_context_messages(session["id"], 10)
    context_after = get_context_messages_after_sequence(session["id"], 0)

    assert [message["content"] for message in visible_messages] == ["visible prompt"]
    assert recent_context == context_after
    assert recent_context[0]["sequence"] == saved["sequence"]
    assert recent_context[0]["message"]["role"] == "assistant"
    assert recent_context[0]["message"]["tool_calls"][0]["id"] == "call_read"


def test_session_plans_supersede_pending_plans(client):
    session = client.post("/sessions", json={"title": "Plans"}).json()
    first_prompt = save_message(
        session_id=session["id"],
        role="user",
        content="first prompt",
    )
    first_plan_message = save_message(
        session_id=session["id"],
        role="agent",
        content="first plan",
    )
    first_plan = create_plan(
        session_id=session["id"],
        prompt_message_id=first_prompt["id"],
        plan_message_id=first_plan_message["id"],
        content="first plan",
    )

    second_prompt = save_message(
        session_id=session["id"],
        role="user",
        content="second prompt",
    )
    second_plan_message = save_message(
        session_id=session["id"],
        role="agent",
        content="second plan",
    )
    second_plan = create_plan(
        session_id=session["id"],
        prompt_message_id=second_prompt["id"],
        plan_message_id=second_plan_message["id"],
        content="second plan",
    )

    assert fetch_plan(session["id"], first_plan["id"])["status"] == "superseded"
    assert fetch_plan(session["id"], second_plan["id"])["status"] == "pending"

    approved = approve_plan(session["id"], second_plan["id"])
    assert approved["status"] == "approved"
    executed = mark_plan_executed(session["id"], second_plan["id"])
    assert executed["status"] == "executed"


def test_session_messages_include_plan_metadata(client):
    session = client.post("/sessions", json={"title": "Plan metadata"}).json()
    prompt = save_message(session_id=session["id"], role="user", content="prompt")
    plan_message = save_message(session_id=session["id"], role="agent", content="plan")
    plan = create_plan(
        session_id=session["id"],
        prompt_message_id=prompt["id"],
        plan_message_id=plan_message["id"],
        content="plan",
    )

    messages = client.get(f"/sessions/{session['id']}/messages").json()

    assert messages[0]["plan_id"] is None
    assert messages[0]["plan_status"] is None
    assert messages[1]["id"] == plan_message["id"]
    assert messages[1]["plan_id"] == plan["id"]
    assert messages[1]["plan_status"] == "pending"


def test_session_delete_cascades_plans(client):
    session = client.post("/sessions", json={"title": "Plan cascade"}).json()
    prompt = save_message(session_id=session["id"], role="user", content="prompt")
    save_tool_run_message(
        session_id=session["id"],
        tool_call_id="call_rg",
        tool="rg",
        arguments="{}",
    )
    plan_message = save_message(session_id=session["id"], role="agent", content="plan")
    plan = create_plan(
        session_id=session["id"],
        prompt_message_id=prompt["id"],
        plan_message_id=plan_message["id"],
        content="plan",
    )

    assert fetch_plan(session["id"], plan["id"])["status"] == "pending"
    assert [message["role"] for message in list_messages(session["id"])] == [
        "user",
        "tool",
        "agent",
    ]
    assert client.delete(f"/sessions/{session['id']}").status_code == 204

    try:
        fetch_plan(session["id"], plan["id"])
    except SessionNotFoundError:
        pass
    except PlanNotFoundError:
        pass
    else:
        raise AssertionError("Plan should be removed with its session")


def test_init_db_resets_legacy_messages_schema(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AUTOMATA_DATA_DIR", str(tmp_path))
    db_file = tmp_path / "automata.db"
    with sqlite3.connect(db_file) as db:
        db.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'agent')),
                content TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                UNIQUE (session_id, sequence)
            );

            CREATE TABLE session_plans (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                prompt_message_id TEXT NOT NULL,
                plan_message_id TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('pending', 'approved', 'executed', 'superseded')
                ),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                approved_at TEXT,
                executed_at TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                FOREIGN KEY (prompt_message_id) REFERENCES messages(id) ON DELETE CASCADE,
                FOREIGN KEY (plan_message_id) REFERENCES messages(id) ON DELETE CASCADE
            );

            INSERT INTO sessions (id, title, created_at, updated_at)
            VALUES ('session-1', 'Legacy', '2026-06-04T00:00:00', '2026-06-04T00:00:00');

            INSERT INTO messages (id, session_id, role, content, sequence, created_at)
            VALUES ('message-1', 'session-1', 'user', 'legacy prompt', 1, '2026-06-04T00:00:00');

            INSERT INTO messages (id, session_id, role, content, sequence, created_at)
            VALUES ('message-2', 'session-1', 'agent', 'legacy plan', 2, '2026-06-04T00:00:01');

            INSERT INTO session_plans (
                id,
                session_id,
                prompt_message_id,
                plan_message_id,
                content,
                status,
                created_at,
                updated_at
            )
            VALUES (
                'plan-1',
                'session-1',
                'message-1',
                'message-2',
                'legacy plan',
                'pending',
                '2026-06-04T00:00:01',
                '2026-06-04T00:00:01'
            );
            """
        )

    init_db()

    assert client_orphan_session_is_gone()
    with sqlite3.connect(db_file) as db:
        columns = {
            row[1]
            for row in db.execute("PRAGMA table_info(messages)").fetchall()
        }
        assert {"kind", "metadata_json"}.issubset(columns)
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def client_orphan_session_is_gone():
    try:
        list_messages("session-1")
    except SessionNotFoundError:
        return True
    return False
