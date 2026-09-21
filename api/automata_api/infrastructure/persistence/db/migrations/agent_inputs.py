from __future__ import annotations

import sqlite3


def add_agent_inputs(db: sqlite3.Connection) -> None:
    """Persist steering and follow-up inputs without weakening Run locking."""
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_inputs (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            delivery TEXT NOT NULL CHECK (delivery IN ('steer', 'queue')),
            status TEXT NOT NULL CHECK (
                status IN ('pending', 'applying', 'applied', 'cancelled', 'rejected')
            ),
            mode TEXT NOT NULL CHECK (mode IN ('act', 'plan')),
            prompt TEXT NOT NULL,
            skills_json TEXT,
            target_run_id TEXT,
            predecessor_run_id TEXT,
            run_id TEXT,
            message_id TEXT,
            position INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            applied_at TEXT,
            error_code TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
            FOREIGN KEY (target_run_id) REFERENCES agent_runs(id) ON DELETE SET NULL,
            FOREIGN KEY (predecessor_run_id) REFERENCES agent_runs(id)
                ON DELETE SET NULL,
            FOREIGN KEY (run_id) REFERENCES agent_runs(id) ON DELETE SET NULL,
            FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE SET NULL,
            UNIQUE (session_id, request_id),
            UNIQUE (session_id, position)
        )
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_agent_inputs_target_status_position
        ON agent_inputs(target_run_id, status, position)
        """
    )
    db.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_agent_inputs_session_status_position
        ON agent_inputs(session_id, status, position)
        """
    )


__all__ = ["add_agent_inputs"]
