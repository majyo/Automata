from __future__ import annotations

import os
import sys
from pathlib import Path


def managed_runtime_roots() -> tuple[Path, ...]:
    if os.name != "nt":
        return ()
    api_root = Path(__file__).resolve().parents[4]
    candidates = {
        api_root,
        Path(sys.base_prefix).resolve(),
        Path(sys.executable).resolve().parent,
    }
    extraction_root = getattr(sys, "_MEIPASS", None)
    if isinstance(extraction_root, str) and extraction_root:
        candidates.add(Path(extraction_root).resolve())
    return tuple(sorted(candidates, key=lambda path: str(path).lower()))
