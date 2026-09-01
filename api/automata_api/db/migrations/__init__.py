from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from .context_search import add_agent_context_search


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


# The current development database schema is created directly from
# automata_api.db.baseline. Derived search state still has a migration because
# existing context messages must be indexed without losing the conversation.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "add_agent_context_search", add_agent_context_search),
)
