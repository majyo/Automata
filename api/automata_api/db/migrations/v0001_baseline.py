from __future__ import annotations

import sqlite3

from automata_api.agent.prompts import agent_workspace


class UnknownBaselineSchemaError(RuntimeError):
    pass


SESSIONS_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    working_directory TEXT NOT NULL,
    backend TEXT NOT NULL DEFAULT 'local',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

MESSAGES_SQL = """
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
)
"""

CONTEXT_MESSAGES_SQL = """
CREATE TABLE IF NOT EXISTS agent_context_messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    message_json TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    UNIQUE (session_id, sequence)
)
"""

SUMMARIES_SQL = """
CREATE TABLE IF NOT EXISTS session_context_summaries (
    session_id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    through_sequence INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
)
"""

PLANS_SQL = """
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
)
"""


def apply(db: sqlite3.Connection) -> None:
    tables = table_names(db)
    if "sessions" not in tables:
        if tables:
            raise UnknownBaselineSchemaError(
                "Existing database has no sessions table and cannot be adopted safely."
            )
        create_baseline(db)
        return

    validate_known_table(
        db,
        "sessions",
        {"id", "title", "created_at", "updated_at"},
    )
    if "messages" in tables:
        validate_known_table(
            db,
            "messages",
            {"id", "session_id", "role", "content", "sequence", "created_at"},
        )
    if "session_plans" in tables:
        validate_known_table(
            db,
            "session_plans",
            {
                "id",
                "session_id",
                "prompt_message_id",
                "plan_message_id",
                "content",
                "status",
                "created_at",
                "updated_at",
                "approved_at",
                "executed_at",
            },
        )

    migrate_sessions(db)
    if "messages" not in tables:
        db.execute(MESSAGES_SQL)
    else:
        migrate_messages(db)
    db.execute(CONTEXT_MESSAGES_SQL)
    db.execute(SUMMARIES_SQL)
    db.execute(PLANS_SQL)
    create_indexes(db)


def create_baseline(db: sqlite3.Connection) -> None:
    db.execute(SESSIONS_SQL)
    db.execute(MESSAGES_SQL)
    db.execute(CONTEXT_MESSAGES_SQL)
    db.execute(SUMMARIES_SQL)
    db.execute(PLANS_SQL)
    create_indexes(db)


def migrate_sessions(db: sqlite3.Connection) -> None:
    columns = column_names(db, "sessions")
    if "working_directory" not in columns:
        db.execute("ALTER TABLE sessions ADD COLUMN working_directory TEXT")
    if "backend" not in columns:
        db.execute(
            "ALTER TABLE sessions ADD COLUMN backend TEXT NOT NULL DEFAULT 'local'"
        )
    db.execute(
        """
        UPDATE sessions
        SET working_directory = ?
        WHERE working_directory IS NULL OR TRIM(working_directory) = ''
        """,
        (agent_workspace(),),
    )
    db.execute(
        """
        UPDATE sessions
        SET backend = 'local'
        WHERE backend IS NULL OR TRIM(backend) = ''
        """
    )


def migrate_messages(db: sqlite3.Connection) -> None:
    columns = column_names(db, "messages")
    if {"kind", "metadata_json"}.issubset(columns):
        return

    row_count = int(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
    db.execute("DROP TABLE IF EXISTS messages_new")
    db.execute(MESSAGES_SQL.replace("messages", "messages_new", 1))
    kind_expression = "kind" if "kind" in columns else "'message'"
    metadata_expression = "metadata_json" if "metadata_json" in columns else "NULL"
    db.execute(
        f"""
        INSERT INTO messages_new (
            id, session_id, role, kind, content, metadata_json, sequence, created_at
        )
        SELECT
            id,
            session_id,
            role,
            {kind_expression},
            content,
            {metadata_expression},
            sequence,
            created_at
        FROM messages
        ORDER BY session_id, sequence
        """
    )
    copied_count = int(
        db.execute("SELECT COUNT(*) FROM messages_new").fetchone()[0]
    )
    if copied_count != row_count:
        raise RuntimeError("Legacy message migration did not preserve every row.")
    db.execute("DROP TABLE messages")
    db.execute("ALTER TABLE messages_new RENAME TO messages")


def create_indexes(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_messages_session_sequence
        ON messages(session_id, sequence)
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_context_messages_session_sequence
        ON agent_context_messages(session_id, sequence)
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_session_plans_session_status
        ON session_plans(session_id, status)
        """
    )


def table_names(db: sqlite3.Connection) -> set[str]:
    rows = db.execute(
        """
        SELECT name
        FROM sqlite_schema
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        """
    ).fetchall()
    return {str(row[0]) for row in rows if row[0] != "schema_migrations"}


def column_names(db: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row["name"])
        for row in db.execute(f'PRAGMA table_info("{table}")').fetchall()
    }


def validate_known_table(
    db: sqlite3.Connection, table: str, required: set[str]
) -> None:
    missing = required - column_names(db, table)
    if missing:
        raise UnknownBaselineSchemaError(
            f"Table {table} is missing required columns: {sorted(missing)}"
        )
