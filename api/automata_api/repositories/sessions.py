import sqlite3
from typing import Any

from automata_api.db.connection import connect_db, db_lock
from automata_api.utils import new_id, normalize_title, now_iso


class SessionNotFoundError(ValueError):
    pass


def list_sessions() -> list[dict[str, Any]]:
    with db_lock, connect_db() as db:
        rows = db.execute(
            """
            SELECT
                sessions.id,
                sessions.title,
                sessions.created_at,
                sessions.updated_at,
                COUNT(messages.id) AS message_count
            FROM sessions
            LEFT JOIN messages ON messages.session_id = sessions.id
            GROUP BY sessions.id
            ORDER BY sessions.updated_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def create_session(title: str | None) -> dict[str, Any]:
    session_title = normalize_title(title)
    session_id = new_id()
    now = now_iso()

    with db_lock, connect_db() as db:
        db.execute(
            """
            INSERT INTO sessions (id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, session_title, now, now),
        )
        db.commit()

    return {
        "id": session_id,
        "title": session_title,
        "created_at": now,
        "updated_at": now,
        "message_count": 0,
    }


def update_session(session_id: str, title: str) -> dict[str, Any]:
    session_title = normalize_title(title)
    now = now_iso()

    with db_lock, connect_db() as db:
        cursor = db.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (session_title, now, session_id),
        )
        if cursor.rowcount == 0:
            raise SessionNotFoundError("Session not found")
        db.commit()

        row = fetch_session(db, session_id)
        if row is None:
            raise SessionNotFoundError("Session not found")
        return row


def delete_session(session_id: str) -> None:
    with db_lock, connect_db() as db:
        cursor = db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        if cursor.rowcount == 0:
            raise SessionNotFoundError("Session not found")
        db.commit()


def list_messages(session_id: str) -> list[dict[str, Any]]:
    with db_lock, connect_db() as db:
        ensure_session(db, session_id)
        rows = db.execute(
            """
            SELECT id, session_id, role, content, sequence, created_at
            FROM messages
            WHERE session_id = ?
            ORDER BY sequence ASC
            """,
            (session_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def session_exists(session_id: str) -> bool:
    with db_lock, connect_db() as db:
        row = db.execute(
            "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return row is not None


def save_message(session_id: str, role: str, content: str) -> dict[str, Any]:
    message_id = new_id()
    created_at = now_iso()

    with db_lock, connect_db() as db:
        row = db.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        sequence = int(row["next_sequence"])
        db.execute(
            """
            INSERT INTO messages (id, session_id, role, content, sequence, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (message_id, session_id, role, content, sequence, created_at),
        )
        db.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (created_at, session_id)
        )
        db.commit()

    return {
        "id": message_id,
        "session_id": session_id,
        "role": role,
        "content": content,
        "sequence": sequence,
        "created_at": created_at,
    }


def get_recent_messages(session_id: str, limit: int) -> list[dict[str, str]]:
    with db_lock, connect_db() as db:
        rows = db.execute(
            """
            SELECT role, content
            FROM messages
            WHERE session_id = ?
            ORDER BY sequence DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()

    return [dict(row) for row in reversed(rows)]


def get_messages_after_sequence(
    session_id: str, sequence: int
) -> list[dict[str, Any]]:
    with db_lock, connect_db() as db:
        rows = db.execute(
            """
            SELECT role, content, sequence
            FROM messages
            WHERE session_id = ? AND sequence > ?
            ORDER BY sequence ASC
            """,
            (session_id, sequence),
        ).fetchall()

    return [dict(row) for row in rows]


def fetch_context_summary(session_id: str) -> dict[str, Any] | None:
    with db_lock, connect_db() as db:
        row = db.execute(
            """
            SELECT session_id, content, through_sequence, created_at, updated_at
            FROM session_context_summaries
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()

    return dict(row) if row else None


def upsert_context_summary(
    session_id: str, content: str, through_sequence: int
) -> dict[str, Any]:
    now = now_iso()

    with db_lock, connect_db() as db:
        existing = db.execute(
            """
            SELECT created_at
            FROM session_context_summaries
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        created_at = existing["created_at"] if existing else now
        db.execute(
            """
            INSERT INTO session_context_summaries (
                session_id, content, through_sequence, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                content = excluded.content,
                through_sequence = excluded.through_sequence,
                updated_at = excluded.updated_at
            """,
            (session_id, content, through_sequence, created_at, now),
        )
        db.commit()

    return {
        "session_id": session_id,
        "content": content,
        "through_sequence": through_sequence,
        "created_at": created_at,
        "updated_at": now,
    }


def fetch_session(
    db: sqlite3.Connection, session_id: str
) -> dict[str, Any] | None:
    row = db.execute(
        """
        SELECT
            sessions.id,
            sessions.title,
            sessions.created_at,
            sessions.updated_at,
            COUNT(messages.id) AS message_count
        FROM sessions
        LEFT JOIN messages ON messages.session_id = sessions.id
        WHERE sessions.id = ?
        GROUP BY sessions.id
        """,
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


def ensure_session(db: sqlite3.Connection, session_id: str) -> None:
    if fetch_session(db, session_id) is None:
        raise SessionNotFoundError("Session not found")
