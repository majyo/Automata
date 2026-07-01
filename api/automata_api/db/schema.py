from automata_api.agent.prompts import agent_workspace
from automata_api.db.connection import connect_db, db_lock, db_path


MESSAGES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS messages (
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
"""

SESSION_PLANS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS session_plans (
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
"""

AGENT_CONTEXT_MESSAGES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS agent_context_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    message_json TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    UNIQUE (session_id, sequence)
);
"""

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_messages_session_sequence
ON messages(session_id, sequence);

CREATE INDEX IF NOT EXISTS idx_agent_context_messages_session_sequence
ON agent_context_messages(session_id, sequence);

CREATE INDEX IF NOT EXISTS idx_session_plans_session_status
ON session_plans(session_id, status);
"""

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    working_directory TEXT NOT NULL,
    backend TEXT NOT NULL DEFAULT 'local',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

{MESSAGES_TABLE_SQL}

{AGENT_CONTEXT_MESSAGES_TABLE_SQL}

CREATE TABLE IF NOT EXISTS session_context_summaries (
    session_id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    through_sequence INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

{SESSION_PLANS_TABLE_SQL}

{INDEX_SQL}
"""


def init_db() -> None:
    db_path().parent.mkdir(parents=True, exist_ok=True)
    with db_lock, connect_db() as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA foreign_keys = ON")
        if messages_schema_is_legacy(db):
            reset_app_tables(db)
        db.executescript(SCHEMA_SQL)
        migrate_sessions_working_directory(db)
        migrate_sessions_backend(db)
        db.commit()


def messages_schema_is_legacy(db) -> bool:
    row = db.execute(
        """
        SELECT 1
        FROM sqlite_schema
        WHERE type = 'table' AND name = 'messages'
        """
    ).fetchone()
    if row is None:
        return False

    columns = {
        str(column["name"])
        for column in db.execute("PRAGMA table_info(messages)").fetchall()
    }
    return "kind" not in columns or "metadata_json" not in columns


def migrate_sessions_working_directory(db) -> None:
    row = db.execute(
        """
        SELECT 1
        FROM sqlite_schema
        WHERE type = 'table' AND name = 'sessions'
        """
    ).fetchone()
    if row is None:
        return

    columns = {
        str(column["name"])
        for column in db.execute("PRAGMA table_info(sessions)").fetchall()
    }
    if "working_directory" in columns:
        db.execute(
            """
            UPDATE sessions
            SET working_directory = ?
            WHERE working_directory IS NULL OR TRIM(working_directory) = ''
            """,
            (agent_workspace(),),
        )
        return

    db.execute("ALTER TABLE sessions ADD COLUMN working_directory TEXT")
    db.execute(
        """
        UPDATE sessions
        SET working_directory = ?
        WHERE working_directory IS NULL OR TRIM(working_directory) = ''
        """,
        (agent_workspace(),),
    )


def migrate_sessions_backend(db) -> None:
    row = db.execute(
        """
        SELECT 1
        FROM sqlite_schema
        WHERE type = 'table' AND name = 'sessions'
        """
    ).fetchone()
    if row is None:
        return

    columns = {
        str(column["name"])
        for column in db.execute("PRAGMA table_info(sessions)").fetchall()
    }
    if "backend" in columns:
        db.execute(
            """
            UPDATE sessions
            SET backend = 'local'
            WHERE backend IS NULL OR TRIM(backend) = ''
            """
        )
        return

    db.execute("ALTER TABLE sessions ADD COLUMN backend TEXT NOT NULL DEFAULT 'local'")
    db.execute(
        """
        UPDATE sessions
        SET backend = 'local'
        WHERE backend IS NULL OR TRIM(backend) = ''
        """
    )


def reset_app_tables(db) -> None:
    db.commit()
    db.execute("PRAGMA foreign_keys = OFF")
    db.executescript(
        """
        DROP INDEX IF EXISTS idx_messages_session_sequence;
        DROP INDEX IF EXISTS idx_agent_context_messages_session_sequence;
        DROP INDEX IF EXISTS idx_session_plans_session_status;
        DROP TABLE IF EXISTS session_context_summaries;
        DROP TABLE IF EXISTS agent_context_messages;
        DROP TABLE IF EXISTS session_plans;
        DROP TABLE IF EXISTS messages;
        DROP TABLE IF EXISTS sessions;
        """
    )
    db.commit()
    db.execute("PRAGMA foreign_keys = ON")
