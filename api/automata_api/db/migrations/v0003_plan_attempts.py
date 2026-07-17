from __future__ import annotations

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute("DROP TABLE IF EXISTS session_plans_new")
    db.execute(
        """
        CREATE TABLE session_plans_new (
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
        """
    )
    source_count = int(
        db.execute("SELECT COUNT(*) FROM session_plans").fetchone()[0]
    )
    db.execute(
        """
        INSERT INTO session_plans_new (
            id,
            session_id,
            prompt_message_id,
            plan_message_id,
            content,
            status,
            created_at,
            updated_at,
            approved_at,
            executed_at
        )
        SELECT
            id,
            session_id,
            prompt_message_id,
            plan_message_id,
            content,
            CASE status
                WHEN 'approved' THEN 'failed'
                ELSE status
            END,
            created_at,
            updated_at,
            approved_at,
            executed_at
        FROM session_plans
        """
    )
    copied_count = int(
        db.execute("SELECT COUNT(*) FROM session_plans_new").fetchone()[0]
    )
    if copied_count != source_count:
        raise RuntimeError("Plan migration did not preserve every row.")

    db.execute("DROP TABLE session_plans")
    db.execute("ALTER TABLE session_plans_new RENAME TO session_plans")
    db.execute(
        """
        CREATE INDEX idx_session_plans_session_status
        ON session_plans(session_id, status)
        """
    )
    db.execute(
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
        """
    )
    db.execute(
        """
        CREATE INDEX ix_plan_execution_attempts_plan_created
        ON plan_execution_attempts(plan_id, created_at DESC)
        """
    )
