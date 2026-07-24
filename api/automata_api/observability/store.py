from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class ObservabilityStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection: sqlite3.Connection | None = None

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS profile_sessions (
                id TEXT PRIMARY KEY,
                boot_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                capture_content INTEGER NOT NULL,
                pid INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                clean_shutdown INTEGER NOT NULL DEFAULT 0,
                artifact_path TEXT
            );

            CREATE TABLE IF NOT EXISTS traces (
                trace_id TEXT PRIMARY KEY,
                profile_session_id TEXT,
                run_id TEXT,
                session_id TEXT,
                root_span_id TEXT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                duration_ns INTEGER,
                status TEXT,
                FOREIGN KEY (profile_session_id)
                    REFERENCES profile_sessions(id) ON DELETE SET NULL
            );

            CREATE INDEX IF NOT EXISTS ix_observability_traces_run
            ON traces(run_id, started_at);

            CREATE TABLE IF NOT EXISTS spans (
                trace_id TEXT NOT NULL,
                span_id TEXT NOT NULL,
                parent_span_id TEXT,
                name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                duration_ns INTEGER NOT NULL,
                status TEXT NOT NULL,
                attributes_json TEXT NOT NULL,
                error_type TEXT,
                PRIMARY KEY (trace_id, span_id),
                FOREIGN KEY (trace_id)
                    REFERENCES traces(trace_id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS ix_observability_spans_name_started
            ON spans(name, started_at);

            CREATE TABLE IF NOT EXISTS collector_stats (
                profile_session_id TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            """
        )
        connection.commit()
        self.connection = connection

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def write_batch(self, records: list[dict[str, Any]]) -> None:
        if self.connection is None or not records:
            return
        connection = self.connection
        for record in records:
            record_type = record.get("record_type")
            if record_type == "profile_session_start":
                connection.execute(
                    """
                    INSERT OR REPLACE INTO profile_sessions (
                        id, boot_id, mode, capture_content, pid,
                        started_at, artifact_path
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.get("profile_session_id"),
                        record.get("boot_id"),
                        record.get("mode"),
                        int(record.get("capture_content") is True),
                        record.get("pid"),
                        record.get("timestamp_utc"),
                        record.get("artifact_path"),
                    ),
                )
            elif record_type == "profile_session_end":
                connection.execute(
                    """
                    UPDATE profile_sessions
                    SET ended_at = ?, clean_shutdown = 1
                    WHERE id = ?
                    """,
                    (
                        record.get("timestamp_utc"),
                        record.get("profile_session_id"),
                    ),
                )
            elif record_type == "trace_start":
                connection.execute(
                    """
                    INSERT OR REPLACE INTO traces (
                        trace_id, profile_session_id, run_id, session_id,
                        root_span_id, started_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.get("trace_id"),
                        record.get("profile_session_id"),
                        record.get("run_id"),
                        record.get("session_id"),
                        record.get("span_id"),
                        record.get("timestamp_utc"),
                    ),
                )
            elif record_type == "trace_end":
                connection.execute(
                    """
                    UPDATE traces
                    SET ended_at = ?, duration_ns = ?, status = ?
                    WHERE trace_id = ?
                    """,
                    (
                        record.get("timestamp_utc"),
                        record.get("duration_ns"),
                        record.get("status"),
                        record.get("trace_id"),
                    ),
                )
            elif record_type == "span_end":
                connection.execute(
                    """
                    INSERT OR REPLACE INTO spans (
                        trace_id, span_id, parent_span_id, name,
                        started_at, ended_at, duration_ns, status,
                        attributes_json, error_type
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.get("trace_id"),
                        record.get("span_id"),
                        record.get("parent_span_id"),
                        record.get("name"),
                        record.get("started_at"),
                        record.get("timestamp_utc"),
                        record.get("duration_ns"),
                        record.get("status"),
                        json.dumps(
                            record.get("attributes", {}),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        record.get("error_type"),
                    ),
                )
            elif record_type == "collector_health":
                connection.execute(
                    """
                    INSERT INTO collector_stats (
                        profile_session_id, recorded_at, payload_json
                    )
                    VALUES (?, ?, ?)
                    """,
                    (
                        record.get("profile_session_id") or record.get("boot_id"),
                        record.get("timestamp_utc"),
                        json.dumps(
                            record.get("attributes", {}),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
        connection.commit()

    def prune_before(self, cutoff_iso: str) -> None:
        if self.connection is None:
            return
        connection = self.connection
        connection.execute(
            "DELETE FROM spans WHERE ended_at < ?",
            (cutoff_iso,),
        )
        connection.execute(
            "DELETE FROM traces WHERE COALESCE(ended_at, started_at) < ?",
            (cutoff_iso,),
        )
        connection.execute(
            "DELETE FROM collector_stats WHERE recorded_at < ?",
            (cutoff_iso,),
        )
        connection.execute(
            """
            DELETE FROM profile_sessions
            WHERE COALESCE(ended_at, started_at) < ?
            """,
            (cutoff_iso,),
        )
        connection.commit()

    def prune_missing_profile_artifacts(self) -> None:
        if self.connection is None:
            return
        connection = self.connection
        rows = connection.execute(
            """
            SELECT id, artifact_path
            FROM profile_sessions
            WHERE artifact_path IS NOT NULL
            """
        ).fetchall()
        missing = [
            str(row[0])
            for row in rows
            if not Path(str(row[1])).exists()
        ]
        for profile_session_id in missing:
            connection.execute(
                """
                DELETE FROM spans
                WHERE trace_id IN (
                    SELECT trace_id
                    FROM traces
                    WHERE profile_session_id = ?
                )
                """,
                (profile_session_id,),
            )
            connection.execute(
                "DELETE FROM traces WHERE profile_session_id = ?",
                (profile_session_id,),
            )
            connection.execute(
                """
                DELETE FROM collector_stats
                WHERE profile_session_id = ?
                """,
                (profile_session_id,),
            )
            connection.execute(
                "DELETE FROM profile_sessions WHERE id = ?",
                (profile_session_id,),
            )
        connection.commit()
