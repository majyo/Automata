from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from automata_api.db.migrations import (
    v0001_baseline,
    v0002_runs,
    v0003_plan_attempts,
    v0004_permission_presets,
)


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


MIGRATIONS = (
    Migration(1, "adopt_or_upgrade_baseline", v0001_baseline.apply),
    Migration(2, "add_durable_runs", v0002_runs.apply),
    Migration(3, "add_plan_attempts", v0003_plan_attempts.apply),
    Migration(4, "add_permission_presets", v0004_permission_presets.apply),
)
