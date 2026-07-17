from __future__ import annotations

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute(
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
            FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
            FOREIGN KEY (request_message_id) REFERENCES messages(id) ON DELETE SET NULL,
            FOREIGN KEY (response_message_id) REFERENCES messages(id) ON DELETE SET NULL,
            FOREIGN KEY (plan_id) REFERENCES session_plans(id) ON DELETE SET NULL
        )
        """
    )
    db.execute(
        """
        CREATE UNIQUE INDEX ux_agent_runs_one_active_per_session
        ON agent_runs(session_id)
        WHERE status IN ('queued', 'running', 'waiting_approval', 'cancelling')
        """
    )
    db.execute(
        """
        CREATE INDEX ix_agent_runs_session_created
        ON agent_runs(session_id, created_at DESC)
        """
    )
    db.execute(
        """
        CREATE INDEX ix_agent_runs_status_created
        ON agent_runs(status, created_at DESC)
        """
    )
    db.execute(
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
        """
    )
    db.execute(
        """
        CREATE INDEX ix_agent_run_events_created_at
        ON agent_run_events(created_at)
        """
    )
