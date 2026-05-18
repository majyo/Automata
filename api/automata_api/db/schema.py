from automata_api.db.connection import connect_db, db_lock, db_path


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'agent')),
    content TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    UNIQUE (session_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_messages_session_sequence
ON messages(session_id, sequence);

CREATE TABLE IF NOT EXISTS session_context_summaries (
    session_id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    through_sequence INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
"""


def init_db() -> None:
    db_path().parent.mkdir(parents=True, exist_ok=True)
    with db_lock, connect_db() as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript(SCHEMA_SQL)
        db.commit()
