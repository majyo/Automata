import sqlite3
from pathlib import Path
from threading import Lock

from automata_api.config import get_database_config


db_lock = Lock()


def db_path() -> Path:
    return get_database_config().path


def connect_db() -> sqlite3.Connection:
    connection = sqlite3.connect(db_path(), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection
