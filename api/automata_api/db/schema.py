from __future__ import annotations

import hashlib
import inspect
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from automata_api.db.connection import connect_db, db_lock, db_path
from automata_api.db.migrations import MIGRATIONS, Migration


class DatabaseMigrationError(RuntimeError):
    pass


class DatabaseSchemaTooNewError(DatabaseMigrationError):
    pass


class DatabaseMigrationChecksumError(DatabaseMigrationError):
    pass


def init_db() -> None:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    with db_lock, connect_db() as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA foreign_keys = ON")
        assert_database_integrity(db, "before migration")
        has_user_objects = database_has_user_objects(db)
        has_migration_table = table_exists(db, "schema_migrations")
        backup_path: Path | None = None
        if has_user_objects and not has_migration_table:
            backup_path = create_database_backup(db, path, {}, list(MIGRATIONS))
        applied = applied_migrations(db) if has_migration_table else {}
        validate_applied_migrations(applied)

        pending = [migration for migration in MIGRATIONS if migration.version not in applied]
        if pending and has_user_objects and backup_path is None:
            backup_path = create_database_backup(db, path, applied, pending)

        try:
            if pending:
                db.execute("PRAGMA foreign_keys = OFF")
                for migration in pending:
                    apply_migration(db, migration)
                db.execute("PRAGMA foreign_keys = ON")

            assert_database_integrity(db, "after migration")
        except Exception:
            db.execute("PRAGMA foreign_keys = ON")
            if backup_path is not None and backup_path.exists():
                backup_path.rename(backup_path.with_name(f"{backup_path.name}.failed"))
            raise

        prune_successful_backups(path, keep=3)


def ensure_migration_table(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    db.commit()


def applied_migrations(db: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    rows = db.execute(
        """
        SELECT version, name, checksum, applied_at
        FROM schema_migrations
        ORDER BY version ASC
        """
    ).fetchall()
    return {int(row["version"]): row for row in rows}


def validate_applied_migrations(applied: dict[int, sqlite3.Row]) -> None:
    known = {migration.version: migration for migration in MIGRATIONS}
    if applied and max(applied) > max(known):
        raise DatabaseSchemaTooNewError(
            f"Database schema version {max(applied)} is newer than supported "
            f"version {max(known)}."
        )

    expected_versions = list(range(1, max(applied, default=0) + 1))
    if sorted(applied) != expected_versions:
        raise DatabaseMigrationError("Database migration history contains a gap.")

    for version, row in applied.items():
        migration = known.get(version)
        if migration is None:
            raise DatabaseSchemaTooNewError(
                f"Database migration {version} is not supported by this application."
            )
        checksum = migration_checksum(migration)
        if row["name"] != migration.name or row["checksum"] != checksum:
            raise DatabaseMigrationChecksumError(
                f"Database migration {version} does not match the application."
            )


def apply_migration(db: sqlite3.Connection, migration: Migration) -> None:
    checksum = migration_checksum(migration)
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        migration.apply(db)
        db.execute(
            """
            INSERT INTO schema_migrations (version, name, checksum, applied_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                migration.version,
                migration.name,
                checksum,
                datetime.now(UTC).isoformat(),
            ),
        )
        assert_database_integrity(db, f"migration {migration.version}")
        db.commit()
    except Exception:
        db.rollback()
        raise


def migration_checksum(migration: Migration) -> str:
    source_path = inspect.getsourcefile(migration.apply)
    if source_path is None:
        raise DatabaseMigrationError(
            f"Cannot locate migration source for version {migration.version}."
        )
    source = Path(source_path).read_bytes()
    return hashlib.sha256(source).hexdigest()


def assert_database_integrity(db: sqlite3.Connection, stage: str) -> None:
    quick_check = db.execute("PRAGMA quick_check").fetchone()
    if quick_check is None or str(quick_check[0]).lower() != "ok":
        detail = quick_check[0] if quick_check else "no result"
        raise DatabaseMigrationError(
            f"Database quick_check failed {stage}: {detail}"
        )

    violations = db.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise DatabaseMigrationError(
            f"Database foreign_key_check failed {stage}: {len(violations)} violation(s)."
        )


def database_has_user_objects(db: sqlite3.Connection) -> bool:
    row = db.execute(
        """
        SELECT 1
        FROM sqlite_schema
        WHERE name NOT LIKE 'sqlite_%'
          AND name != 'schema_migrations'
        LIMIT 1
        """
    ).fetchone()
    return row is not None


def table_exists(db: sqlite3.Connection, table: str) -> bool:
    row = db.execute(
        """
        SELECT 1
        FROM sqlite_schema
        WHERE type = 'table' AND name = ?
        """,
        (table,),
    ).fetchone()
    return row is not None


def create_database_backup(
    db: sqlite3.Connection,
    path: Path,
    applied: dict[int, sqlite3.Row],
    pending: list[Migration],
) -> Path:
    db.execute("PRAGMA wal_checkpoint(FULL)")
    from_version = max(applied, default=0)
    to_version = pending[-1].version
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = path.with_name(
        f"{path.name}.backup.{timestamp}.v{from_version}-to-v{to_version}"
    )
    backup = sqlite3.connect(backup_path)
    try:
        db.backup(backup)
    finally:
        backup.close()
    return backup_path


def prune_successful_backups(path: Path, *, keep: int) -> None:
    backups = sorted(
        (
            candidate
            for candidate in path.parent.glob(f"{path.name}.backup.*")
            if not candidate.name.endswith(".failed")
        ),
        key=lambda candidate: candidate.stat().st_mtime,
        reverse=True,
    )
    for stale in backups[keep:]:
        stale.unlink(missing_ok=True)
