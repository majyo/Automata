import json
import sqlite3
from pathlib import Path
from typing import Any

from automata_api.agent.backends.factory import (
    available_backend_kinds,
    default_backend_kind,
)
from automata_api.agent.prompts import agent_workspace
from automata_api.db.connection import connect_db, db_lock
from automata_api.utils import new_id, normalize_title, now_iso


class SessionNotFoundError(ValueError):
    pass


class InvalidWorkingDirectoryError(ValueError):
    pass


class InvalidBackendError(ValueError):
    pass


class PlanNotFoundError(ValueError):
    pass


class PlanStateError(ValueError):
    pass


class SessionHasActiveRunError(ValueError):
    def __init__(self, run_id: str) -> None:
        super().__init__("Session has an active run.")
        self.run_id = run_id


def list_sessions() -> list[dict[str, Any]]:
    with db_lock, connect_db() as db:
        rows = db.execute(
            """
            SELECT
                sessions.id,
                sessions.title,
                sessions.working_directory,
                sessions.backend,
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


def create_session(
    title: str | None,
    working_directory: str | None = None,
    backend: str | None = None,
) -> dict[str, Any]:
    session_title = normalize_title(title)
    resolved_working_directory = normalize_working_directory(working_directory)
    resolved_backend = normalize_backend(backend)
    session_id = new_id()
    now = now_iso()

    with db_lock, connect_db() as db:
        db.execute(
            """
            INSERT INTO sessions (
                id, title, working_directory, backend, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                session_title,
                resolved_working_directory,
                resolved_backend,
                now,
                now,
            ),
        )
        db.commit()

    return {
        "id": session_id,
        "title": session_title,
        "working_directory": resolved_working_directory,
        "backend": resolved_backend,
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
        active_run = db.execute(
            """
            SELECT id
            FROM agent_runs
            WHERE session_id = ?
              AND status IN ('queued', 'running', 'waiting_approval', 'cancelling')
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if active_run is not None:
            raise SessionHasActiveRunError(str(active_run["id"]))
        cursor = db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        if cursor.rowcount == 0:
            raise SessionNotFoundError("Session not found")
        db.commit()


def list_messages(session_id: str) -> list[dict[str, Any]]:
    with db_lock, connect_db() as db:
        ensure_session(db, session_id)
        rows = db.execute(
            """
            SELECT
                messages.id,
                messages.session_id,
                messages.role,
                messages.kind,
                messages.content,
                messages.metadata_json,
                messages.sequence,
                messages.created_at,
                session_plans.id AS plan_id,
                session_plans.status AS plan_status
            FROM messages
            LEFT JOIN session_plans
                ON session_plans.plan_message_id = messages.id
            WHERE messages.session_id = ?
            ORDER BY sequence ASC
            """,
            (session_id,),
        ).fetchall()
        return [message_row_from_db(row) for row in rows]


def session_exists(session_id: str) -> bool:
    with db_lock, connect_db() as db:
        row = db.execute(
            "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return row is not None


def session_working_directory(session_id: str) -> str:
    with db_lock, connect_db() as db:
        row = fetch_session(db, session_id)
        if row is None:
            raise SessionNotFoundError("Session not found")
        return str(row["working_directory"])


def session_backend_config(session_id: str) -> dict[str, str]:
    with db_lock, connect_db() as db:
        row = fetch_session(db, session_id)
        if row is None:
            raise SessionNotFoundError("Session not found")
        return {
            "working_directory": str(row["working_directory"]),
            "backend": str(row["backend"] or "local"),
        }


def save_message(
    session_id: str,
    role: str,
    content: str,
    *,
    kind: str = "message",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message_id = new_id()
    created_at = now_iso()
    metadata_json = (
        json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        if metadata is not None
        else None
    )

    with db_lock, connect_db() as db:
        row = db.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        sequence = int(row["next_sequence"])
        db.execute(
            """
            INSERT INTO messages (
                id,
                session_id,
                role,
                kind,
                content,
                metadata_json,
                sequence,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                session_id,
                role,
                kind,
                content,
                metadata_json,
                sequence,
                created_at,
            ),
        )
        db.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?", (created_at, session_id)
        )
        db.commit()

    return {
        "id": message_id,
        "session_id": session_id,
        "role": role,
        "kind": kind,
        "content": content,
        "metadata": metadata,
        "sequence": sequence,
        "created_at": created_at,
    }


def save_tool_run_message(
    *,
    session_id: str,
    tool_call_id: str,
    tool: str,
    arguments: str,
) -> dict[str, Any]:
    return save_message(
        session_id=session_id,
        role="tool",
        kind="tool_run",
        content="",
        metadata={
            "tool_call_id": tool_call_id,
            "tool": tool,
            "arguments": arguments,
            "result": None,
        },
    )


def update_tool_run_result(
    *,
    session_id: str,
    message_id: str,
    success: bool,
    content: str,
) -> dict[str, Any]:
    with db_lock, connect_db() as db:
        ensure_session(db, session_id)
        row = db.execute(
            """
            SELECT
                id,
                session_id,
                role,
                kind,
                content,
                metadata_json,
                sequence,
                created_at
            FROM messages
            WHERE session_id = ? AND id = ? AND kind = 'tool_run'
            """,
            (session_id, message_id),
        ).fetchone()
        if row is None:
            raise ValueError("Tool run message not found")

        message = message_row_from_db(row)
        metadata = message.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["result"] = {"success": success, "content": content}
        metadata_json = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        db.execute(
            """
            UPDATE messages
            SET metadata_json = ?
            WHERE session_id = ? AND id = ?
            """,
            (metadata_json, session_id, message_id),
        )
        db.commit()

    message["metadata"] = metadata
    return message


def save_context_message(session_id: str, message: dict[str, Any]) -> dict[str, Any]:
    message_id = new_id()
    created_at = now_iso()
    message_json = json.dumps(message, ensure_ascii=False, sort_keys=True)

    with db_lock, connect_db() as db:
        ensure_session(db, session_id)
        row = db.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
            FROM agent_context_messages
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        sequence = int(row["next_sequence"])
        db.execute(
            """
            INSERT INTO agent_context_messages (
                id, session_id, message_json, sequence, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (message_id, session_id, message_json, sequence, created_at),
        )
        db.commit()

    return {
        "id": message_id,
        "session_id": session_id,
        "message": message,
        "sequence": sequence,
        "created_at": created_at,
    }


def get_recent_messages(session_id: str, limit: int) -> list[dict[str, Any]]:
    with db_lock, connect_db() as db:
        rows = db.execute(
            """
            SELECT role, kind, content, metadata_json, sequence
            FROM messages
            WHERE session_id = ?
            ORDER BY sequence DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()

    return [message_row_from_db(row) for row in reversed(rows)]


def get_recent_context_messages(session_id: str, limit: int) -> list[dict[str, Any]]:
    with db_lock, connect_db() as db:
        rows = db.execute(
            """
            SELECT message_json, sequence
            FROM agent_context_messages
            WHERE session_id = ?
            ORDER BY sequence DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()

    return [context_row_from_db(row) for row in reversed(rows)]


def get_messages_after_sequence(
    session_id: str, sequence: int
) -> list[dict[str, Any]]:
    with db_lock, connect_db() as db:
        rows = db.execute(
            """
            SELECT role, kind, content, metadata_json, sequence
            FROM messages
            WHERE session_id = ? AND sequence > ?
            ORDER BY sequence ASC
            """,
            (session_id, sequence),
        ).fetchall()

    return [message_row_from_db(row) for row in rows]


def get_context_messages_after_sequence(
    session_id: str, sequence: int
) -> list[dict[str, Any]]:
    with db_lock, connect_db() as db:
        rows = db.execute(
            """
            SELECT message_json, sequence
            FROM agent_context_messages
            WHERE session_id = ? AND sequence > ?
            ORDER BY sequence ASC
            """,
            (session_id, sequence),
        ).fetchall()

    return [context_row_from_db(row) for row in rows]


def context_row_from_db(row: sqlite3.Row) -> dict[str, Any]:
    try:
        message = json.loads(row["message_json"])
    except json.JSONDecodeError as error:
        raise RuntimeError("Stored agent context message is invalid JSON.") from error

    if not isinstance(message, dict):
        raise RuntimeError("Stored agent context message must be a JSON object.")

    return {"message": message, "sequence": int(row["sequence"])}


def message_row_from_db(row: sqlite3.Row) -> dict[str, Any]:
    row_dict = dict(row)
    metadata_json = row_dict.pop("metadata_json", None)
    metadata = None
    if isinstance(metadata_json, str) and metadata_json:
        try:
            metadata = json.loads(metadata_json)
        except json.JSONDecodeError as error:
            raise RuntimeError("Stored message metadata is invalid JSON.") from error
        if not isinstance(metadata, dict):
            raise RuntimeError("Stored message metadata must be a JSON object.")

    row_dict["kind"] = row_dict.get("kind") or "message"
    row_dict["metadata"] = metadata
    return row_dict


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


def create_plan(
    *,
    session_id: str,
    prompt_message_id: str,
    plan_message_id: str,
    content: str,
) -> dict[str, Any]:
    plan_id = new_id()
    now = now_iso()

    with db_lock, connect_db() as db:
        ensure_session(db, session_id)
        db.execute(
            """
            UPDATE session_plans
            SET status = 'superseded', updated_at = ?
            WHERE session_id = ? AND status IN ('pending', 'failed')
            """,
            (now, session_id),
        )
        db.execute(
            """
            INSERT INTO session_plans (
                id,
                session_id,
                prompt_message_id,
                plan_message_id,
                content,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                plan_id,
                session_id,
                prompt_message_id,
                plan_message_id,
                content,
                now,
                now,
            ),
        )
        db.commit()

    return {
        "id": plan_id,
        "session_id": session_id,
        "prompt_message_id": prompt_message_id,
        "plan_message_id": plan_message_id,
        "content": content,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "approved_at": None,
        "executed_at": None,
    }


def fetch_plan(session_id: str, plan_id: str) -> dict[str, Any]:
    with db_lock, connect_db() as db:
        ensure_session(db, session_id)
        row = db.execute(
            """
            SELECT
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
            FROM session_plans
            WHERE session_id = ? AND id = ?
            """,
            (session_id, plan_id),
        ).fetchone()

    if row is None:
        raise PlanNotFoundError("Plan not found")

    return dict(row)


def approve_plan(session_id: str, plan_id: str) -> dict[str, Any]:
    now = now_iso()

    with db_lock, connect_db() as db:
        ensure_session(db, session_id)
        row = db.execute(
            """
            SELECT
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
            FROM session_plans
            WHERE session_id = ? AND id = ?
            """,
            (session_id, plan_id),
        ).fetchone()
        if row is None:
            raise PlanNotFoundError("Plan not found")
        if row["status"] != "pending":
            raise PlanStateError(f"Plan is not pending: {row['status']}")

        db.execute(
            """
            UPDATE session_plans
            SET status = 'executing', updated_at = ?, approved_at = ?
            WHERE session_id = ? AND id = ?
            """,
            (now, now, session_id, plan_id),
        )
        db.commit()

    plan = dict(row)
    plan["status"] = "executing"
    plan["updated_at"] = now
    plan["approved_at"] = now
    return plan


def mark_plan_executed(session_id: str, plan_id: str) -> dict[str, Any]:
    now = now_iso()

    with db_lock, connect_db() as db:
        ensure_session(db, session_id)
        row = db.execute(
            """
            SELECT
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
            FROM session_plans
            WHERE session_id = ? AND id = ?
            """,
            (session_id, plan_id),
        ).fetchone()
        if row is None:
            raise PlanNotFoundError("Plan not found")
        if row["status"] != "executing":
            raise PlanStateError(f"Plan is not executing: {row['status']}")

        db.execute(
            """
            UPDATE session_plans
            SET status = 'executed', updated_at = ?, executed_at = ?
            WHERE session_id = ? AND id = ?
            """,
            (now, now, session_id, plan_id),
        )
        db.commit()

    plan = dict(row)
    plan["status"] = "executed"
    plan["updated_at"] = now
    plan["executed_at"] = now
    return plan


def fetch_session(
    db: sqlite3.Connection, session_id: str
) -> dict[str, Any] | None:
    row = db.execute(
        """
        SELECT
            sessions.id,
            sessions.title,
            sessions.working_directory,
            sessions.backend,
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


def normalize_working_directory(working_directory: str | None) -> str:
    raw_value = (
        working_directory.strip()
        if isinstance(working_directory, str) and working_directory.strip()
        else agent_workspace()
    )
    try:
        path = Path(raw_value).expanduser().resolve()
    except OSError as error:
        raise InvalidWorkingDirectoryError(
            f"Working directory is invalid: {raw_value}"
        ) from error

    if not path.exists():
        raise InvalidWorkingDirectoryError(
            f"Working directory does not exist: {path}"
        )
    if not path.is_dir():
        raise InvalidWorkingDirectoryError(
            f"Working directory is not a directory: {path}"
        )

    return str(path)


def normalize_backend(backend: str | None) -> str:
    raw_value = (
        backend.strip().lower()
        if isinstance(backend, str) and backend.strip()
        else default_backend_kind()
    )
    if raw_value not in available_backend_kinds():
        allowed = ", ".join(available_backend_kinds())
        raise InvalidBackendError(
            f"Backend is invalid: {raw_value}. Available backends: {allowed}"
        )
    return raw_value
