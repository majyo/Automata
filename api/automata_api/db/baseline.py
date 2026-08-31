from __future__ import annotations

import sqlite3


class DatabaseBaselineError(RuntimeError):
    pass


TABLE_SQL = (
    """
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        working_directory TEXT NOT NULL,
        backend TEXT NOT NULL DEFAULT 'local',
        permission_preset TEXT NOT NULL DEFAULT 'default'
            CHECK (permission_preset IN ('default', 'full_access')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE messages (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('user', 'agent', 'tool')),
        kind TEXT NOT NULL DEFAULT 'message'
            CHECK (kind IN ('message', 'tool_run')),
        content TEXT NOT NULL,
        metadata_json TEXT,
        sequence INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
        UNIQUE (session_id, sequence)
    )
    """,
    """
    CREATE TABLE agent_context_messages (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        message_json TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
        UNIQUE (session_id, sequence)
    )
    """,
    """
    CREATE TABLE session_context_summaries (
        session_id TEXT PRIMARY KEY,
        content TEXT NOT NULL,
        through_sequence INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE session_plans (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        prompt_message_id TEXT NOT NULL,
        plan_message_id TEXT NOT NULL,
        content TEXT NOT NULL,
        status TEXT NOT NULL CHECK (
            status IN ('pending', 'executing', 'failed', 'executed', 'superseded')
        ),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        approved_at TEXT,
        executed_at TEXT,
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
        FOREIGN KEY (prompt_message_id) REFERENCES messages(id) ON DELETE CASCADE,
        FOREIGN KEY (plan_message_id) REFERENCES messages(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE agent_runs (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (
            kind IN ('chat_act', 'chat_plan', 'plan_execution')
        ),
        mode TEXT NOT NULL CHECK (mode IN ('act', 'plan')),
        status TEXT NOT NULL CHECK (
            status IN (
                'queued',
                'running',
                'waiting_approval',
                'cancelling',
                'completed',
                'failed',
                'cancelled',
                'interrupted'
            )
        ),
        request_message_id TEXT,
        response_message_id TEXT,
        plan_id TEXT,
        owner_instance_id TEXT NOT NULL,
        last_sequence INTEGER NOT NULL DEFAULT 0,
        error_code TEXT,
        public_error TEXT,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        heartbeat_at TEXT,
        permission_preset TEXT NOT NULL DEFAULT 'default'
            CHECK (permission_preset IN ('default', 'full_access')),
        permission_profile_version INTEGER,
        permission_profile_json TEXT,
        sandbox_backend TEXT,
        FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
        FOREIGN KEY (request_message_id) REFERENCES messages(id) ON DELETE SET NULL,
        FOREIGN KEY (response_message_id) REFERENCES messages(id) ON DELETE SET NULL,
        FOREIGN KEY (plan_id) REFERENCES session_plans(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE agent_run_events (
        run_id TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        category TEXT NOT NULL DEFAULT 'runtime'
            CHECK (category IN ('runtime', 'trace')),
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        payload_bytes INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (run_id, sequence),
        FOREIGN KEY (run_id) REFERENCES agent_runs(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE plan_execution_attempts (
        id TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL,
        run_id TEXT NOT NULL UNIQUE,
        attempt_no INTEGER NOT NULL,
        request_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (plan_id) REFERENCES session_plans(id) ON DELETE CASCADE,
        FOREIGN KEY (run_id) REFERENCES agent_runs(id) ON DELETE CASCADE,
        UNIQUE (plan_id, attempt_no),
        UNIQUE (plan_id, request_id)
    )
    """,
)

INDEX_SQL = (
    """
    CREATE INDEX idx_messages_session_sequence
    ON messages(session_id, sequence)
    """,
    """
    CREATE INDEX idx_agent_context_messages_session_sequence
    ON agent_context_messages(session_id, sequence)
    """,
    """
    CREATE INDEX idx_session_plans_session_status
    ON session_plans(session_id, status)
    """,
    """
    CREATE UNIQUE INDEX ux_agent_runs_one_active_per_session
    ON agent_runs(session_id)
    WHERE status IN ('queued', 'running', 'waiting_approval', 'cancelling')
    """,
    """
    CREATE INDEX ix_agent_runs_session_created
    ON agent_runs(session_id, created_at DESC)
    """,
    """
    CREATE INDEX ix_agent_runs_status_created
    ON agent_runs(status, created_at DESC)
    """,
    """
    CREATE INDEX ix_agent_run_events_created_at
    ON agent_run_events(created_at)
    """,
    """
    CREATE INDEX ix_plan_execution_attempts_plan_created
    ON plan_execution_attempts(plan_id, created_at DESC)
    """,
)

EXPECTED_COLUMNS = {
    "sessions": {
        "id",
        "title",
        "working_directory",
        "backend",
        "permission_preset",
        "created_at",
        "updated_at",
    },
    "messages": {
        "id",
        "session_id",
        "role",
        "kind",
        "content",
        "metadata_json",
        "sequence",
        "created_at",
    },
    "agent_context_messages": {
        "id",
        "session_id",
        "message_json",
        "sequence",
        "created_at",
    },
    "session_context_summaries": {
        "session_id",
        "content",
        "through_sequence",
        "created_at",
        "updated_at",
    },
    "session_plans": {
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
    "agent_runs": {
        "id",
        "session_id",
        "kind",
        "mode",
        "status",
        "request_message_id",
        "response_message_id",
        "plan_id",
        "owner_instance_id",
        "last_sequence",
        "error_code",
        "public_error",
        "created_at",
        "started_at",
        "finished_at",
        "heartbeat_at",
        "permission_preset",
        "permission_profile_version",
        "permission_profile_json",
        "sandbox_backend",
    },
    "agent_run_events": {
        "run_id",
        "sequence",
        "category",
        "event_type",
        "payload_json",
        "payload_bytes",
        "created_at",
    },
    "plan_execution_attempts": {
        "id",
        "plan_id",
        "run_id",
        "attempt_no",
        "request_id",
        "created_at",
    },
}


def create_current_schema(db: sqlite3.Connection) -> None:
    try:
        db.execute("BEGIN IMMEDIATE")
        for statement in TABLE_SQL:
            db.execute(statement)
        for statement in INDEX_SQL:
            db.execute(statement)
        db.commit()
    except Exception:
        db.rollback()
        raise


def validate_current_schema(db: sqlite3.Connection) -> None:
    tables = _table_names(db)
    expected_tables = set(EXPECTED_COLUMNS)
    missing_tables = expected_tables - tables
    if missing_tables:
        raise DatabaseBaselineError(
            "Database does not match the current baseline. "
            "Historical database upgrades are not supported; reset the database. "
            f"Missing tables {sorted(missing_tables)}."
        )

    for table, expected_columns in EXPECTED_COLUMNS.items():
        actual_columns = _column_names(db, table)
        missing_columns = expected_columns - actual_columns
        if missing_columns:
            raise DatabaseBaselineError(
                "Database does not match the current baseline. "
                "Historical database upgrades are not supported; reset the database. "
                f"Table {table} is missing columns {sorted(missing_columns)}."
            )


def has_application_tables(db: sqlite3.Connection) -> bool:
    return bool(_table_names(db))


def _table_names(db: sqlite3.Connection) -> set[str]:
    rows = db.execute(
        """
        SELECT name
        FROM sqlite_schema
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
          AND name != 'schema_migrations'
        """
    ).fetchall()
    return {str(row[0]) for row in rows}


def _column_names(db: sqlite3.Connection, table: str) -> set[str]:
    quoted_table = table.replace('"', '""')
    return {
        str(row["name"])
        for row in db.execute(
            f'PRAGMA table_info("{quoted_table}")'
        ).fetchall()
    }
