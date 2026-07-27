from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from automata_api.db.connection import connect_db, db_lock
from automata_api.utils import new_id, now_iso

RunKind = Literal["chat_act", "chat_plan", "plan_execution"]
RunMode = Literal["act", "plan"]
RunStatus = Literal[
    "queued",
    "running",
    "waiting_approval",
    "cancelling",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
]

NON_TERMINAL_STATUSES = (
    "queued",
    "running",
    "waiting_approval",
    "cancelling",
)
TERMINAL_STATUSES = ("completed", "failed", "cancelled", "interrupted")


class RunNotFoundError(ValueError):
    pass


class RunStateError(ValueError):
    pass


class SessionBusyError(ValueError):
    def __init__(self, run_id: str) -> None:
        super().__init__("This session already has an active run.")
        self.run_id = run_id


class PlanNotRetryableError(ValueError):
    pass


class EventCursorError(ValueError):
    pass


def create_prompt_run(
    *,
    session_id: str,
    prompt: str,
    mode: RunMode,
    owner_instance_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    run_id = new_id()
    message_id = new_id()
    now = now_iso()
    kind: RunKind = "chat_plan" if mode == "plan" else "chat_act"

    with db_lock, connect_db() as db:
        try:
            db.execute("BEGIN IMMEDIATE")
            ensure_session(db, session_id)
            message_sequence = next_message_sequence(db, session_id)
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
                VALUES (?, ?, 'user', 'message', ?, NULL, ?, ?)
                """,
                (message_id, session_id, prompt, message_sequence, now),
            )
            db.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            insert_run(
                db,
                run_id=run_id,
                session_id=session_id,
                kind=kind,
                mode=mode,
                owner_instance_id=owner_instance_id,
                request_message_id=message_id,
            )
            db.commit()
        except sqlite3.IntegrityError as error:
            db.rollback()
            existing = active_run_for_session_in_db(db, session_id)
            if existing is not None:
                raise SessionBusyError(str(existing["id"])) from error
            raise

    return (
        get_run(run_id),
        {
            "id": message_id,
            "session_id": session_id,
            "role": "user",
            "kind": "message",
            "content": prompt,
            "metadata": None,
            "sequence": message_sequence,
            "created_at": now,
        },
    )


def create_run(
    *,
    run_id: str | None = None,
    session_id: str,
    kind: RunKind,
    mode: RunMode,
    owner_instance_id: str,
    request_message_id: str | None = None,
    plan_id: str | None = None,
) -> dict[str, Any]:
    resolved_run_id = run_id or new_id()
    with db_lock, connect_db() as db:
        try:
            db.execute("BEGIN IMMEDIATE")
            ensure_session(db, session_id)
            insert_run(
                db,
                run_id=resolved_run_id,
                session_id=session_id,
                kind=kind,
                mode=mode,
                owner_instance_id=owner_instance_id,
                request_message_id=request_message_id,
                plan_id=plan_id,
            )
            db.commit()
        except sqlite3.IntegrityError as error:
            db.rollback()
            existing = active_run_for_session_in_db(db, session_id)
            if existing is not None:
                raise SessionBusyError(str(existing["id"])) from error
            raise
    return get_run(resolved_run_id)


def get_run(run_id: str) -> dict[str, Any]:
    with db_lock, connect_db() as db:
        row = run_row(db, run_id)
        if row is None:
            raise RunNotFoundError("Run not found")
        return dict(row)


def get_session_run(session_id: str, run_id: str) -> dict[str, Any]:
    with db_lock, connect_db() as db:
        ensure_session(db, session_id)
        row = db.execute(
            """
            SELECT *
            FROM agent_runs
            WHERE id = ? AND session_id = ?
            """,
            (run_id, session_id),
        ).fetchone()
        if row is None:
            raise RunNotFoundError("Run not found")
        return dict(row)


def list_runs(
    *,
    session_id: str | None = None,
    non_terminal_only: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(limit, 500))
    clauses: list[str] = []
    parameters: list[Any] = []
    if session_id is not None:
        clauses.append("session_id = ?")
        parameters.append(session_id)
    if non_terminal_only:
        clauses.append(
            "status IN ('queued', 'running', 'waiting_approval', 'cancelling')"
        )
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    parameters.append(safe_limit)

    with db_lock, connect_db() as db:
        if session_id is not None:
            ensure_session(db, session_id)
        rows = db.execute(
            f"""
            SELECT *
            FROM agent_runs
            {where}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        return [dict(row) for row in rows]


def active_run_for_session(session_id: str) -> dict[str, Any] | None:
    with db_lock, connect_db() as db:
        row = active_run_for_session_in_db(db, session_id)
        return dict(row) if row is not None else None


def transition_run(
    run_id: str,
    *,
    expected: tuple[RunStatus, ...],
    target: RunStatus,
) -> dict[str, Any]:
    now = now_iso()
    placeholders = ", ".join("?" for _ in expected)
    started_at = now if target == "running" else None
    with db_lock, connect_db() as db:
        cursor = db.execute(
            f"""
            UPDATE agent_runs
            SET
                status = ?,
                heartbeat_at = ?,
                started_at = CASE
                    WHEN ? IS NOT NULL THEN COALESCE(started_at, ?)
                    ELSE started_at
                END
            WHERE id = ? AND status IN ({placeholders})
            """,
            (
                target,
                now,
                started_at,
                started_at,
                run_id,
                *expected,
            ),
        )
        if cursor.rowcount != 1:
            row = run_row(db, run_id)
            if row is None:
                raise RunNotFoundError("Run not found")
            raise RunStateError(
                f"Run cannot transition from {row['status']} to {target}."
            )
        db.commit()
    return get_run(run_id)


def append_event(
    run_id: str,
    payload: dict[str, Any],
    *,
    category: Literal["runtime", "trace"] = "runtime",
    max_payload_bytes: int = 65_536,
) -> dict[str, Any]:
    with db_lock, connect_db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = run_row(db, run_id)
        if row is None:
            db.rollback()
            raise RunNotFoundError("Run not found")
        event = prepare_event(row, payload, int(row["last_sequence"]) + 1)
        encoded = encode_event(event, max_payload_bytes=max_payload_bytes)
        created_at = now_iso()
        db.execute(
            """
            UPDATE agent_runs
            SET last_sequence = ?, heartbeat_at = ?
            WHERE id = ?
            """,
            (event["seq"], created_at, run_id),
        )
        db.execute(
            """
            INSERT INTO agent_run_events (
                run_id,
                sequence,
                category,
                event_type,
                payload_json,
                payload_bytes,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                event["seq"],
                category,
                event["type"],
                encoded,
                len(encoded.encode("utf-8")),
                created_at,
            ),
        )
        db.commit()
        return event


def finish_run(
    run_id: str,
    *,
    status: Literal["completed", "failed", "cancelled", "interrupted"],
    event: dict[str, Any],
    response_message_id: str | None = None,
    plan_id: str | None = None,
    response_content: str | None = None,
    plan_content: str | None = None,
    error_code: str | None = None,
    public_error: str | None = None,
) -> dict[str, Any]:
    with db_lock, connect_db() as db:
        db.execute("BEGIN IMMEDIATE")
        row = run_row(db, run_id)
        if row is None:
            db.rollback()
            raise RunNotFoundError("Run not found")
        if row["status"] in TERMINAL_STATUSES:
            existing = latest_event_in_db(db, run_id)
            db.commit()
            return existing or prepare_event(
                row, event, int(row["last_sequence"])
            )

        now = now_iso()
        resolved_response_message_id = response_message_id
        response_message: dict[str, Any] | None = None
        if response_content is not None:
            resolved_response_message_id = new_id()
            message_sequence = next_message_sequence(db, str(row["session_id"]))
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
                VALUES (?, ?, 'agent', 'message', ?, NULL, ?, ?)
                """,
                (
                    resolved_response_message_id,
                    row["session_id"],
                    response_content,
                    message_sequence,
                    now,
                ),
            )
            response_message = {
                "id": resolved_response_message_id,
                "session_id": str(row["session_id"]),
                "role": "agent",
                "kind": "message",
                "content": response_content,
                "metadata": None,
                "sequence": message_sequence,
                "created_at": now,
            }

        resolved_plan_id = plan_id
        next_sequence = int(row["last_sequence"]) + 1
        if plan_content is not None:
            if response_message is None or row["request_message_id"] is None:
                db.rollback()
                raise RunStateError(
                    "A Plan run requires an atomic prompt and response message."
                )
            resolved_plan_id = new_id()
            db.execute(
                """
                UPDATE session_plans
                SET status = 'superseded', updated_at = ?
                WHERE session_id = ? AND status IN ('pending', 'failed')
                """,
                (now, row["session_id"]),
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
                    resolved_plan_id,
                    row["session_id"],
                    row["request_message_id"],
                    resolved_response_message_id,
                    plan_content,
                    now,
                    now,
                ),
            )
            plan_ready = prepare_event(
                row,
                {
                    "type": "plan_ready",
                    "plan_id": resolved_plan_id,
                    "status": "pending",
                    "content": plan_content,
                },
                next_sequence,
            )
            insert_encoded_event(db, plan_ready, now=now)
            next_sequence += 1

        terminal_payload = dict(event)
        if terminal_payload.get("type") == "done":
            terminal_payload["message"] = response_message
        prepared = prepare_event(row, terminal_payload, next_sequence)
        encoded = encode_event(prepared)
        db.execute(
            """
            UPDATE agent_runs
            SET
                status = ?,
                response_message_id = COALESCE(?, response_message_id),
                plan_id = COALESCE(?, plan_id),
                last_sequence = ?,
                error_code = ?,
                public_error = ?,
                heartbeat_at = ?,
                finished_at = ?
            WHERE id = ?
            """,
            (
                status,
                resolved_response_message_id,
                resolved_plan_id,
                prepared["seq"],
                error_code,
                public_error,
                now,
                now,
                run_id,
            ),
        )
        db.execute(
            """
            INSERT INTO agent_run_events (
                run_id,
                sequence,
                category,
                event_type,
                payload_json,
                payload_bytes,
                created_at
            )
            VALUES (?, ?, 'runtime', ?, ?, ?, ?)
            """,
            (
                run_id,
                prepared["seq"],
                prepared["type"],
                encoded,
                len(encoded.encode("utf-8")),
                now,
            ),
        )
        db.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (now, row["session_id"]),
        )
        if row["kind"] == "plan_execution" and row["plan_id"]:
            plan_status = "executed" if status == "completed" else "failed"
            db.execute(
                """
                UPDATE session_plans
                SET
                    status = ?,
                    updated_at = ?,
                    executed_at = CASE WHEN ? = 'executed' THEN ? ELSE executed_at END
                WHERE id = ?
                """,
                (plan_status, now, plan_status, now, row["plan_id"]),
            )
        db.commit()
        return prepared


def insert_encoded_event(
    db: sqlite3.Connection,
    event: dict[str, Any],
    *,
    now: str,
    category: Literal["runtime", "trace"] = "runtime",
) -> None:
    encoded = encode_event(event)
    db.execute(
        """
        INSERT INTO agent_run_events (
            run_id,
            sequence,
            category,
            event_type,
            payload_json,
            payload_bytes,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event["run_id"],
            event["seq"],
            category,
            event["type"],
            encoded,
            len(encoded.encode("utf-8")),
            now,
        ),
    )


def list_events(
    run_id: str,
    *,
    after_sequence: int = 0,
    through_sequence: int | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    if after_sequence < 0:
        raise ValueError("after_sequence must be non-negative")
    safe_limit = max(1, min(limit, 1000))
    clauses = ["run_id = ?", "sequence > ?"]
    parameters: list[Any] = [run_id, after_sequence]
    if through_sequence is not None:
        clauses.append("sequence <= ?")
        parameters.append(through_sequence)
    parameters.append(safe_limit)

    with db_lock, connect_db() as db:
        run = run_row(db, run_id)
        if run is None:
            raise RunNotFoundError("Run not found")
        last_sequence = int(run["last_sequence"])
        if after_sequence > last_sequence:
            raise EventCursorError("Event cursor is beyond the Run watermark.")
        earliest = db.execute(
            """
            SELECT MIN(sequence) AS earliest_sequence
            FROM agent_run_events
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        earliest_sequence = (
            int(earliest["earliest_sequence"])
            if earliest is not None and earliest["earliest_sequence"] is not None
            else None
        )
        if (
            earliest_sequence is not None
            and after_sequence < earliest_sequence - 1
        ):
            raise EventCursorError("Event history before this cursor was pruned.")
        rows = db.execute(
            f"""
            SELECT payload_json
            FROM agent_run_events
            WHERE {' AND '.join(clauses)}
            ORDER BY sequence ASC
            LIMIT ?
            """,
            parameters,
        ).fetchall()
    return [decode_event(str(row["payload_json"])) for row in rows]


def prune_terminal_run_events(retention_days: int) -> int:
    safe_days = max(0, retention_days)
    cutoff = (datetime.now(UTC) - timedelta(days=safe_days)).isoformat()
    with db_lock, connect_db() as db:
        db.execute("BEGIN IMMEDIATE")
        cursor = db.execute(
            """
            DELETE FROM agent_run_events
            WHERE created_at < ?
              AND sequence < (
                  SELECT runs.last_sequence
                  FROM agent_runs AS runs
                  WHERE runs.id = agent_run_events.run_id
                    AND runs.status IN (
                        'completed', 'failed', 'cancelled', 'interrupted'
                    )
              )
            """,
            (cutoff,),
        )
        deleted = cursor.rowcount
        db.commit()
        return max(0, deleted)


def interrupt_stale_runs(current_instance_id: str) -> list[dict[str, Any]]:
    interrupted_events: list[dict[str, Any]] = []
    with db_lock, connect_db() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute(
            """
            SELECT *
            FROM agent_runs
            WHERE owner_instance_id != ?
              AND status IN ('queued', 'running', 'waiting_approval', 'cancelling')
            ORDER BY created_at ASC
            """,
            (current_instance_id,),
        ).fetchall()
        for row in rows:
            event = prepare_event(
                row,
                {
                    "type": "run_interrupted",
                    "code": "api_process_restarted",
                    "message": (
                        "The previous API process ended before this run completed."
                    ),
                },
                int(row["last_sequence"]) + 1,
            )
            encoded = encode_event(event)
            now = now_iso()
            db.execute(
                """
                UPDATE agent_runs
                SET
                    status = 'interrupted',
                    last_sequence = ?,
                    error_code = 'api_process_restarted',
                    public_error = ?,
                    heartbeat_at = ?,
                    finished_at = ?
                WHERE id = ?
                """,
                (
                    event["seq"],
                    event["message"],
                    now,
                    now,
                    row["id"],
                ),
            )
            db.execute(
                """
                INSERT INTO agent_run_events (
                    run_id,
                    sequence,
                    category,
                    event_type,
                    payload_json,
                    payload_bytes,
                    created_at
                )
                VALUES (?, ?, 'runtime', ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    event["seq"],
                    event["type"],
                    encoded,
                    len(encoded.encode("utf-8")),
                    now,
                ),
            )
            if row["kind"] == "plan_execution" and row["plan_id"]:
                db.execute(
                    """
                    UPDATE session_plans
                    SET status = 'failed', updated_at = ?
                    WHERE id = ?
                    """,
                    (now, row["plan_id"]),
                )
            interrupted_events.append(event)
        db.commit()
    return interrupted_events


def begin_plan_execution(
    *,
    session_id: str,
    plan_id: str,
    request_id: str,
    owner_instance_id: str,
    retry: bool,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    now = now_iso()
    with db_lock, connect_db() as db:
        db.execute("BEGIN IMMEDIATE")
        ensure_session(db, session_id)
        existing_attempt = db.execute(
            """
            SELECT run_id
            FROM plan_execution_attempts
            WHERE plan_id = ? AND request_id = ?
            """,
            (plan_id, request_id),
        ).fetchone()
        if existing_attempt is not None:
            run = run_row(db, str(existing_attempt["run_id"]))
            plan = plan_row(db, session_id, plan_id)
            db.commit()
            if run is None or plan is None:
                raise RuntimeError("Stored plan attempt is incomplete.")
            return dict(run), dict(plan), True

        plan = plan_row(db, session_id, plan_id)
        if plan is None:
            db.rollback()
            raise PlanNotRetryableError("Plan not found")
        expected_status = "failed" if retry else "pending"
        if plan["status"] != expected_status:
            db.rollback()
            raise PlanNotRetryableError(
                f"Plan is not {expected_status}: {plan['status']}"
            )

        run_id = new_id()
        attempt_id = new_id()
        attempt_row = db.execute(
            """
            SELECT COALESCE(MAX(attempt_no), 0) + 1 AS next_attempt
            FROM plan_execution_attempts
            WHERE plan_id = ?
            """,
            (plan_id,),
        ).fetchone()
        attempt_no = int(attempt_row["next_attempt"])
        try:
            insert_run(
                db,
                run_id=run_id,
                session_id=session_id,
                kind="plan_execution",
                mode="act",
                owner_instance_id=owner_instance_id,
                request_message_id=str(plan["prompt_message_id"]),
                plan_id=plan_id,
            )
        except sqlite3.IntegrityError as error:
            db.rollback()
            existing = active_run_for_session_in_db(db, session_id)
            if existing is not None:
                raise SessionBusyError(str(existing["id"])) from error
            raise

        db.execute(
            """
            INSERT INTO plan_execution_attempts (
                id, plan_id, run_id, attempt_no, request_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (attempt_id, plan_id, run_id, attempt_no, request_id, now),
        )
        db.execute(
            """
            UPDATE session_plans
            SET
                status = 'executing',
                updated_at = ?,
                approved_at = COALESCE(approved_at, ?)
            WHERE id = ?
            """,
            (now, now, plan_id),
        )
        db.commit()

    updated_plan = dict(plan)
    updated_plan["status"] = "executing"
    updated_plan["updated_at"] = now
    updated_plan["approved_at"] = plan["approved_at"] or now
    return get_run(run_id), updated_plan, False


def list_plan_attempts(
    session_id: str, plan_id: str
) -> list[dict[str, Any]]:
    with db_lock, connect_db() as db:
        ensure_session(db, session_id)
        if plan_row(db, session_id, plan_id) is None:
            raise PlanNotRetryableError("Plan not found")
        rows = db.execute(
            """
            SELECT
                attempts.id,
                attempts.plan_id,
                attempts.run_id,
                attempts.attempt_no,
                attempts.request_id,
                attempts.created_at,
                runs.status,
                runs.error_code,
                runs.public_error,
                runs.finished_at
            FROM plan_execution_attempts AS attempts
            JOIN agent_runs AS runs ON runs.id = attempts.run_id
            WHERE attempts.plan_id = ?
            ORDER BY attempts.attempt_no ASC
            """,
            (plan_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def insert_run(
    db: sqlite3.Connection,
    *,
    run_id: str,
    session_id: str,
    kind: RunKind,
    mode: RunMode,
    owner_instance_id: str,
    request_message_id: str | None = None,
    plan_id: str | None = None,
) -> None:
    now = now_iso()
    session = db.execute(
        "SELECT permission_preset FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if session is None:
        raise RunNotFoundError("Session not found")
    db.execute(
        """
        INSERT INTO agent_runs (
            id,
            session_id,
            kind,
            mode,
            permission_preset,
            status,
            request_message_id,
            plan_id,
            owner_instance_id,
            created_at,
            heartbeat_at
        )
        VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            session_id,
            kind,
            mode,
            str(session["permission_preset"]),
            request_message_id,
            plan_id,
            owner_instance_id,
            now,
            now,
        ),
    )


def prepare_event(
    run: sqlite3.Row | dict[str, Any],
    payload: dict[str, Any],
    sequence: int,
) -> dict[str, Any]:
    return {
        **payload,
        "session_id": str(run["session_id"]),
        "run_id": str(run["id"]),
        "seq": sequence,
        "schema_version": 1,
    }


def encode_event(
    event: dict[str, Any], *, max_payload_bytes: int = 65_536
) -> str:
    encoded = json.dumps(
        event,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(encoded.encode("utf-8")) > max_payload_bytes:
        raise ValueError("Run event payload exceeds the configured size limit.")
    return encoded


def decode_event(payload_json: str) -> dict[str, Any]:
    payload = json.loads(payload_json)
    if not isinstance(payload, dict):
        raise RuntimeError("Stored run event must be a JSON object.")
    return payload


def latest_event_in_db(
    db: sqlite3.Connection, run_id: str
) -> dict[str, Any] | None:
    row = db.execute(
        """
        SELECT payload_json
        FROM agent_run_events
        WHERE run_id = ?
        ORDER BY sequence DESC
        LIMIT 1
        """,
        (run_id,),
    ).fetchone()
    return decode_event(str(row["payload_json"])) if row else None


def run_row(db: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    return db.execute(
        "SELECT * FROM agent_runs WHERE id = ?",
        (run_id,),
    ).fetchone()


def plan_row(
    db: sqlite3.Connection, session_id: str, plan_id: str
) -> sqlite3.Row | None:
    return db.execute(
        """
        SELECT *
        FROM session_plans
        WHERE id = ? AND session_id = ?
        """,
        (plan_id, session_id),
    ).fetchone()


def active_run_for_session_in_db(
    db: sqlite3.Connection, session_id: str
) -> sqlite3.Row | None:
    return db.execute(
        """
        SELECT *
        FROM agent_runs
        WHERE session_id = ?
          AND status IN ('queued', 'running', 'waiting_approval', 'cancelling')
        ORDER BY created_at ASC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()


def next_message_sequence(db: sqlite3.Connection, session_id: str) -> int:
    row = db.execute(
        """
        SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
        FROM messages
        WHERE session_id = ?
        """,
        (session_id,),
    ).fetchone()
    return int(row["next_sequence"])


def ensure_session(db: sqlite3.Connection, session_id: str) -> None:
    row = db.execute(
        "SELECT 1 FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Session not found")
