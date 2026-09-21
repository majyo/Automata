"""Resolve environment paths and prepare per-run sandbox permissions."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from automata_api.core.tools.permissions import (
    CompiledPermissionProfile,
    compile_permission_profile,
)


def compile_run_permission_profile(
    value: object,
    *,
    workspace: str | Path,
    run_id: str | None = None,
) -> CompiledPermissionProfile:
    from automata_api.config import env_file_candidates, get_database_config

    sensitive: list[Path] = [get_database_config().path.parent]
    sensitive.extend(path for path in env_file_candidates() if path.exists())
    temporary_paths: tuple[Path, ...] | None = None
    if run_id is not None:
        run_key = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:32]
        run_temp = Path(tempfile.gettempdir()) / "automata-sandbox" / "runs" / run_key
        run_temp.mkdir(parents=True, exist_ok=True)
        temporary_paths = (run_temp,)
    return compile_permission_profile(
        value,
        workspace=workspace,
        sensitive_paths=tuple(sensitive),
        temporary_paths=temporary_paths,
    )
