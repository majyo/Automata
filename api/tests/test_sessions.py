import sqlite3
from pathlib import Path

import pytest

from automata_api.agent.backends.factory import default_backend_kind
from automata_api.agent.prompts import agent_workspace
from automata_api.db.schema import (
    DatabaseMigrationChecksumError,
    DatabaseMigrationError,
    DatabaseSchemaTooNewError,
    init_db,
)
from automata_api.repositories.sessions import (
    PlanNotFoundError,
    SessionNotFoundError,
    approve_plan,
    create_plan,
    fetch_context_summary,
    fetch_plan,
    get_context_messages_after_sequence,
    get_recent_context_messages,
    list_messages,
    mark_plan_executed,
    save_context_message,
    save_message,
    save_tool_run_message,
    update_tool_run_result,
    upsert_context_summary,
)


def test_session_crud_and_messages(client):
    assert client.get("/sessions").json() == []

    created = client.post("/sessions", json={"title": "Alpha"}).json()
    assert created["title"] == "Alpha"
    assert created["permission_preset"] == "default"
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
    assert updated["permission_preset"] == "default"

    messages = client.get(f"/sessions/{created['id']}/messages")
    assert messages.status_code == 200
    assert messages.json() == []

    deleted = client.delete(f"/sessions/{created['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/sessions/{created['id']}/messages").status_code == 404


def test_create_session_persists_working_directory(client, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()

    created = client.post(
        "/sessions",
        json={"title": "Workspace", "working_directory": str(workspace)},
    ).json()

    assert created["working_directory"] == str(workspace.resolve())
    sessions = client.get("/sessions").json()
    assert sessions[0]["working_directory"] == str(workspace.resolve())


def test_create_session_persists_backend(client, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()

    created = client.post(
        "/sessions",
        json={
            "title": "Backend",
            "working_directory": str(workspace),
            "backend": "local",
        },
    ).json()

    assert created["backend"] == "local"
    assert client.get("/sessions").json()[0]["backend"] == "local"


def test_create_session_defaults_backend(client):
    created = client.post("/sessions", json={"title": "Default backend"}).json()

    assert created["backend"] == default_backend_kind()


def test_session_permission_preset_is_persisted_and_patchable(client):
    created = client.post(
        "/sessions",
        json={"title": "Unsafe", "permission_preset": "full_access"},
    ).json()

    assert created["permission_preset"] == "full_access"
    assert client.get("/sessions").json()[0]["permission_preset"] == "full_access"

    updated = client.patch(
        f"/sessions/{created['id']}",
        json={"permission_preset": "default"},
    )

    assert updated.status_code == 200
    assert updated.json()["title"] == "Unsafe"
    assert updated.json()["permission_preset"] == "default"


def test_session_permission_preset_rejects_invalid_and_empty_updates(client):
    created = client.post("/sessions", json={"title": "Permissions"}).json()

    invalid = client.patch(
        f"/sessions/{created['id']}",
        json={"permission_preset": "unrestricted"},
    )
    empty = client.patch(f"/sessions/{created['id']}", json={})

    assert invalid.status_code == 422
    assert empty.status_code == 422


def test_create_session_rejects_invalid_backend(client):
    response = client.post(
        "/sessions",
        json={"title": "Bad backend", "backend": "missing"},
    )

    assert response.status_code == 422
    assert "Backend is invalid" in response.json()["detail"]


def test_create_session_defaults_working_directory(client):
    created = client.post("/sessions", json={"title": "Default workspace"}).json()

    assert Path(created["working_directory"]) == Path(agent_workspace()).resolve()


def test_create_session_rejects_invalid_working_directory(client, tmp_path):
    missing = client.post(
        "/sessions",
        json={"title": "Missing", "working_directory": str(tmp_path / "missing")},
    )
    file_path = tmp_path / "file.txt"
    file_path.write_text("not a directory\n", encoding="utf-8")
    file_response = client.post(
        "/sessions",
        json={"title": "File", "working_directory": str(file_path)},
    )

    assert missing.status_code == 422
    assert file_response.status_code == 422


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
    assert approved["status"] == "executing"
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


def test_init_db_preserves_and_upgrades_legacy_messages_schema(
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

    with sqlite3.connect(db_file) as db:
        columns = {
            row[1]
            for row in db.execute("PRAGMA table_info(messages)").fetchall()
        }
        assert {"kind", "metadata_json"}.issubset(columns)
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM session_plans").fetchone()[0] == 1
        assert db.execute(
            "SELECT content FROM messages WHERE id = 'message-1'"
        ).fetchone()[0] == "legacy prompt"
        assert db.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0] == 4
    assert list_messages("session-1")[0]["content"] == "legacy prompt"
    assert list(tmp_path.glob("automata.db.backup.*"))


def test_init_db_removes_orphaned_legacy_messages_shadow_table_after_backup(
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
                role TEXT NOT NULL CHECK (role IN ('user', 'agent', 'tool')),
                kind TEXT NOT NULL DEFAULT 'message' CHECK (
                    kind IN ('message', 'tool_run')
                ),
                content TEXT NOT NULL,
                metadata_json TEXT,
                sequence INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                UNIQUE (session_id, sequence)
            );

            CREATE TABLE messages_old (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'agent')),
                content TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                UNIQUE (session_id, sequence)
            );

            INSERT INTO sessions (id, title, created_at, updated_at)
            VALUES ('session-1', 'Keep', '2026-07-23', '2026-07-23');

            INSERT INTO messages (
                id, session_id, role, content, sequence, created_at
            )
            VALUES (
                'message-1',
                'session-1',
                'user',
                'keep me',
                1,
                '2026-07-23'
            );

            INSERT INTO messages_old (
                id, session_id, role, content, sequence, created_at
            )
            VALUES (
                'orphan-1',
                'deleted-session',
                'user',
                'legacy orphan',
                1,
                '2026-05-11'
            );
            """
        )

    init_db()

    with sqlite3.connect(db_file) as db:
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute(
            """
            SELECT COUNT(*) FROM sqlite_schema
            WHERE type = 'table' AND name = 'messages_old'
            """
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT content FROM messages WHERE id = 'message-1'"
        ).fetchone()[0] == "keep me"
        assert db.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0] == 4

    backup_file = next(tmp_path.glob("automata.db.backup.*"))
    with sqlite3.connect(backup_file) as backup:
        assert backup.execute(
            "SELECT content FROM messages_old WHERE id = 'orphan-1'"
        ).fetchone()[0] == "legacy orphan"


def test_init_db_rejects_unrecognized_foreign_key_violation(
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
                role TEXT NOT NULL CHECK (role IN ('user', 'agent', 'tool')),
                kind TEXT NOT NULL DEFAULT 'message' CHECK (
                    kind IN ('message', 'tool_run')
                ),
                content TEXT NOT NULL,
                metadata_json TEXT,
                sequence INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                UNIQUE (session_id, sequence)
            );

            INSERT INTO messages (
                id, session_id, role, content, sequence, created_at
            )
            VALUES (
                'orphan-1',
                'deleted-session',
                'user',
                'must not be repaired automatically',
                1,
                '2026-07-23'
            );
            """
        )

    with pytest.raises(
        DatabaseMigrationError,
        match=r"foreign_key_check failed before migration: 1 violation",
    ):
        init_db()

    with sqlite3.connect(db_file) as db:
        assert db.execute(
            "SELECT content FROM messages WHERE id = 'orphan-1'"
        ).fetchone()[0] == "must not be repaired automatically"
        assert db.execute(
            """
            SELECT COUNT(*) FROM sqlite_schema
            WHERE type = 'table' AND name = 'schema_migrations'
            """
        ).fetchone()[0] == 0


def test_init_db_migrates_session_working_directory(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AUTOMATA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOMATA_WORKSPACE_DIR", str(workspace))
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
                role TEXT NOT NULL CHECK (role IN ('user', 'agent', 'tool')),
                kind TEXT NOT NULL DEFAULT 'message' CHECK (kind IN ('message', 'tool_run')),
                content TEXT NOT NULL,
                metadata_json TEXT,
                sequence INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                UNIQUE (session_id, sequence)
            );

            INSERT INTO sessions (id, title, created_at, updated_at)
            VALUES ('session-1', 'Existing', '2026-06-04T00:00:00', '2026-06-04T00:00:00');
            """
        )

    init_db()

    with sqlite3.connect(db_file) as db:
        columns = {
            row[1]
            for row in db.execute("PRAGMA table_info(sessions)").fetchall()
        }
        working_directory = db.execute(
            "SELECT working_directory FROM sessions WHERE id = 'session-1'"
        ).fetchone()[0]
        assert "working_directory" in columns
        assert working_directory == str(workspace)


def test_init_db_migrates_session_backend(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AUTOMATA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOMATA_WORKSPACE_DIR", str(workspace))
    db_file = tmp_path / "automata.db"
    with sqlite3.connect(db_file) as db:
        db.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                working_directory TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'agent', 'tool')),
                kind TEXT NOT NULL DEFAULT 'message' CHECK (kind IN ('message', 'tool_run')),
                content TEXT NOT NULL,
                metadata_json TEXT,
                sequence INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                UNIQUE (session_id, sequence)
            );

            INSERT INTO sessions (
                id, title, working_directory, created_at, updated_at
            )
            VALUES (
                'session-1',
                'Existing',
                'D:/workspace',
                '2026-06-04T00:00:00',
                '2026-06-04T00:00:00'
            );
            """
        )

    init_db()

    with sqlite3.connect(db_file) as db:
        session_columns = {
            row[1]
            for row in db.execute("PRAGMA table_info(sessions)").fetchall()
        }
        run_columns = {
            row[1]
            for row in db.execute("PRAGMA table_info(agent_runs)").fetchall()
        }
        backend = db.execute(
            "SELECT backend FROM sessions WHERE id = 'session-1'"
        ).fetchone()[0]
        permission_preset = db.execute(
            "SELECT permission_preset FROM sessions WHERE id = 'session-1'"
        ).fetchone()[0]
        assert "backend" in session_columns
        assert "permission_preset" in session_columns
        assert "permission_preset" in run_columns
        assert backend == "local"
        assert permission_preset == "default"


def test_init_db_is_idempotent_and_backup_is_readable(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOMATA_DATA_DIR", str(tmp_path))
    db_file = tmp_path / "automata.db"
    with sqlite3.connect(db_file) as db:
        db.execute(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            INSERT INTO sessions (id, title, created_at, updated_at)
            VALUES ('session-1', 'Keep me', '2026-07-17', '2026-07-17')
            """
        )

    init_db()
    first_backup = next(tmp_path.glob("automata.db.backup.*"))
    init_db()

    with sqlite3.connect(db_file) as db:
        assert db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 4
        assert db.execute("SELECT title FROM sessions").fetchone()[0] == "Keep me"
    with sqlite3.connect(first_backup) as backup:
        assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert backup.execute("SELECT title FROM sessions").fetchone()[0] == "Keep me"
    backup_files = [
        path
        for path in tmp_path.glob("automata.db.backup.*")
        if not path.name.endswith(("-wal", "-shm"))
    ]
    assert backup_files == [first_backup]


def test_unknown_database_schema_is_not_modified_and_failed_backup_is_kept(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AUTOMATA_DATA_DIR", str(tmp_path))
    db_file = tmp_path / "automata.db"
    with sqlite3.connect(db_file) as db:
        db.execute("CREATE TABLE mystery (value TEXT NOT NULL)")
        db.execute("INSERT INTO mystery (value) VALUES ('original')")

    with pytest.raises(RuntimeError, match="cannot be adopted safely"):
        init_db()

    with sqlite3.connect(db_file) as db:
        assert db.execute("SELECT value FROM mystery").fetchone()[0] == "original"
        assert db.execute(
            """
            SELECT COUNT(*) FROM sqlite_schema
            WHERE type = 'table' AND name = 'schema_migrations'
            """
        ).fetchone()[0] == 0
    failed_backup = next(tmp_path.glob("automata.db.backup.*.failed"))
    with sqlite3.connect(failed_backup) as backup:
        assert backup.execute("SELECT value FROM mystery").fetchone()[0] == "original"


def test_migration_checksum_mismatch_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOMATA_DATA_DIR", str(tmp_path))
    init_db()
    with sqlite3.connect(tmp_path / "automata.db") as db:
        db.execute(
            "UPDATE schema_migrations SET checksum = 'tampered' WHERE version = 1"
        )

    with pytest.raises(DatabaseMigrationChecksumError):
        init_db()


def test_database_schema_newer_than_application_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOMATA_DATA_DIR", str(tmp_path))
    init_db()
    with sqlite3.connect(tmp_path / "automata.db") as db:
        db.execute(
            """
            INSERT INTO schema_migrations (version, name, checksum, applied_at)
            VALUES (999, 'future', 'future', '2026-07-17')
            """
        )

    with pytest.raises(DatabaseSchemaTooNewError):
        init_db()


def client_orphan_session_is_gone():
    try:
        list_messages("session-1")
    except SessionNotFoundError:
        return True
    return False
