import json
import sqlite3

import pytest

import automata_api.db.schema as schema_module
from automata_api.db.baseline import (
    EXPECTED_COLUMNS,
    DatabaseBaselineError,
    create_current_schema,
)
from automata_api.db.migrations import MIGRATIONS, Migration
from automata_api.db.schema import (
    DatabaseSchemaTooNewError,
    init_db,
)
from automata_api.repositories.sessions import search_context


def _add_future_marker(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE future_marker (value TEXT NOT NULL)")
    db.execute("ALTER TABLE sessions ADD COLUMN future_value TEXT")


def test_fresh_database_uses_current_baseline_and_context_search_migration(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("AUTOMATA_DATA_DIR", str(tmp_path))

    init_db()

    with sqlite3.connect(tmp_path / "automata.db") as db:
        tables = {
            str(row[0])
            for row in db.execute(
                """
                SELECT name
                FROM sqlite_schema
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """
            ).fetchall()
        }
        assert set(EXPECTED_COLUMNS).issubset(tables)
        assert "schema_migrations" in tables
        assert db.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0] == len(MIGRATIONS)
        context_columns = {
            str(row[1])
            for row in db.execute(
                "PRAGMA table_info('agent_context_messages')"
            ).fetchall()
        }
        assert "source" in context_columns
        assert db.execute(
            "SELECT 1 FROM sqlite_schema WHERE name = 'agent_context_search_fts'"
        ).fetchone() is not None
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"

    assert MIGRATIONS


def test_current_baseline_initialization_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOMATA_DATA_DIR", str(tmp_path))

    init_db()
    init_db()

    with sqlite3.connect(tmp_path / "automata.db") as db:
        assert db.execute(
            "SELECT COUNT(*) FROM schema_migrations"
        ).fetchone()[0] == len(MIGRATIONS)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_context_search_migration_preserves_existing_context(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("AUTOMATA_DATA_DIR", str(tmp_path))
    database = tmp_path / "automata.db"
    with sqlite3.connect(database) as db:
        create_current_schema(db)
        db.execute(
            """
            INSERT INTO sessions (id, title, working_directory, created_at, updated_at)
            VALUES ('session', 'Legacy', ?, '2026-09-01', '2026-09-01')
            """,
            (str(tmp_path),),
        )
        db.execute(
            """
            INSERT INTO agent_context_messages (
                id, session_id, message_json, sequence, created_at
            )
            VALUES ('context-1', 'session', ?, 1, '2026-09-01')
            """,
            (json.dumps({"role": "user", "content": "pre-migration-needle"}),),
        )

    init_db()

    result = search_context("session", "pre-migration-needle")
    assert result["returned"] == 1
    assert result["matches"][0]["context_message_id"] == "context-1"


def test_legacy_database_is_rejected_without_modification(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("AUTOMATA_DATA_DIR", str(tmp_path))
    database = tmp_path / "automata.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO sessions (id) VALUES ('legacy')")

    with pytest.raises(
        DatabaseBaselineError,
        match="Historical database upgrades are not supported",
    ):
        init_db()

    with sqlite3.connect(database) as db:
        assert db.execute("SELECT id FROM sessions").fetchone()[0] == "legacy"
        assert db.execute(
            """
            SELECT COUNT(*)
            FROM sqlite_schema
            WHERE type = 'table' AND name = 'schema_migrations'
            """
        ).fetchone()[0] == 0


def test_historical_migration_history_requires_database_reset(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("AUTOMATA_DATA_DIR", str(tmp_path))
    init_db()
    with sqlite3.connect(tmp_path / "automata.db") as db:
        db.execute(
            """
            INSERT INTO schema_migrations (version, name, checksum, applied_at)
            VALUES (99, 'historical', 'historical', '2026-07-31')
            """
        )

    with pytest.raises(
        DatabaseSchemaTooNewError,
        match="Reset the database",
    ):
        init_db()


def test_future_migration_hook_can_apply_and_validate_a_migration(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("AUTOMATA_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        schema_module,
        "MIGRATIONS",
        (Migration(1, "add_future_marker", _add_future_marker),),
    )

    init_db()
    init_db()

    with sqlite3.connect(tmp_path / "automata.db") as db:
        assert db.execute(
            "SELECT name FROM schema_migrations WHERE version = 1"
        ).fetchone()[0] == "add_future_marker"
        assert db.execute(
            """
            SELECT COUNT(*)
            FROM sqlite_schema
            WHERE type = 'table' AND name = 'future_marker'
            """
        ).fetchone()[0] == 1
        assert "future_value" in {
            str(row[1])
            for row in db.execute("PRAGMA table_info(sessions)").fetchall()
        }
