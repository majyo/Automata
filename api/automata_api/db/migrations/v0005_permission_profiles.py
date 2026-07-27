from __future__ import annotations

import sqlite3

from automata_api.agent.execution.permissions import (
    compile_run_permission_profile,
    sandbox_backend_for_profile,
)


def apply(db: sqlite3.Connection) -> None:
    db.execute(
        """
        ALTER TABLE agent_runs
        ADD COLUMN permission_profile_version INTEGER
        """
    )
    rows = db.execute(
        """
        SELECT
            runs.id,
            runs.permission_preset,
            sessions.working_directory
        FROM agent_runs AS runs
        JOIN sessions ON sessions.id = runs.session_id
        """
    ).fetchall()
    for row in rows:
        profile = compile_run_permission_profile(
            row["permission_preset"],
            workspace=row["working_directory"],
            run_id=row["id"],
        )
        db.execute(
            """
            UPDATE agent_runs
            SET
                permission_profile_version = ?,
                permission_profile_json = ?,
                sandbox_backend = ?
            WHERE id = ?
            """,
            (
                profile.version,
                profile.to_json(),
                sandbox_backend_for_profile(profile),
                row["id"],
            ),
        )
    db.execute(
        """
        ALTER TABLE agent_runs
        ADD COLUMN permission_profile_json TEXT
        """
    )
    db.execute(
        """
        ALTER TABLE agent_runs
        ADD COLUMN sandbox_backend TEXT
        """
    )
