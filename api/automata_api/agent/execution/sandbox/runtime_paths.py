"""Compatibility facade: runtime roots moved to ``automata_api.execution``.

The canonical implementation is :mod:`automata_api.execution.runtime_paths`.
This shim is deleted in M7 together with its callers.
"""

from __future__ import annotations

from automata_api.execution.runtime_paths import (
    managed_runtime_roots as managed_runtime_roots,
)
