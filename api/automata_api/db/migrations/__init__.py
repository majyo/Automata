from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


# The current development database schema is created directly from
# automata_api.db.baseline. Add future migrations here when preserving data
# across a baseline change becomes necessary.
MIGRATIONS: tuple[Migration, ...] = ()
