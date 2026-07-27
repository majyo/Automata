from __future__ import annotations

import sqlite3


def apply(db: sqlite3.Connection) -> None:
    db.execute(
        """
        ALTER TABLE sessions
        ADD COLUMN permission_preset TEXT NOT NULL DEFAULT 'default'
            CHECK (permission_preset IN ('default', 'full_access'))
        """
    )
    db.execute(
        """
        ALTER TABLE agent_runs
        ADD COLUMN permission_preset TEXT NOT NULL DEFAULT 'default'
            CHECK (permission_preset IN ('default', 'full_access'))
        """
    )
