"""Regenerate tests/architecture/boundary_baseline.json from the live tree.

Run with::

    uv run --directory api --group dev --locked pytest tests/architecture/test_zz_regen.py -q -s
"""

from __future__ import annotations

import json

from tests.architecture.boundaries import (
    BASELINE_PATH,
    collect_imports,
    cross_module_edges,
    find_import_cycles,
)

COMMENT = [
    "M0 dependency baseline for automata_api.",
    "Each entry is a tolerated cross-module import that predates the",
    "structure refactoring. Entries must only ever be REMOVED: the",
    "architecture test fails when a new violation appears, and also fails",
    "when a recorded entry is no longer needed (so the list cannot rot).",
    "Keys are '<source group> -> <imported group>' at coarse granularity",
    "(first package segment under automata_api, or the file stem for",
    "top-level modules such as main.py).",
]


def test_regenerate_baseline():
    edges = sorted(cross_module_edges(collect_imports()))
    BASELINE_PATH.write_text(
        json.dumps({"_comment": COMMENT, "violations": edges}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(edges)} violations")
    for edge in edges:
        print("  ", edge)
    print("cycles:", find_import_cycles(collect_imports()))
