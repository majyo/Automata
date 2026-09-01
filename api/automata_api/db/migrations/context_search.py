from __future__ import annotations

import sqlite3

from automata_api.db.context_search import (
    CONTEXT_SOURCE_CONVERSATION,
    create_context_search_schema,
    rebuild_context_search_index,
)


def add_agent_context_search(db: sqlite3.Connection) -> None:
    columns = {
        str(row["name"])
        for row in db.execute("PRAGMA table_info('agent_context_messages')")
    }
    if "source" not in columns:
        db.execute(
            """
            ALTER TABLE agent_context_messages
            ADD COLUMN source TEXT NOT NULL DEFAULT 'conversation'
            """
        )

    create_context_search_schema(db)
    rebuild_context_search_index(db)


__all__ = ["CONTEXT_SOURCE_CONVERSATION", "add_agent_context_search"]
