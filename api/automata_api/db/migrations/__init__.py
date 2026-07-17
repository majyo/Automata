from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from automata_api.db.migrations import v0001_baseline
from automata_api.db.migrations import v0002_runs
from automata_api.db.migrations import v0003_plan_attempts


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


MIGRATIONS = (
    Migration(1, "adopt_or_upgrade_baseline", v0001_baseline.apply),
    Migration(2, "add_durable_runs", v0002_runs.apply),
    Migration(3, "add_plan_attempts", v0003_plan_attempts.apply),
)
