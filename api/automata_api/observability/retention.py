from __future__ import annotations

import shutil
import time
from pathlib import Path

from automata_api.observability.config import ObservabilityConfig


def enforce_retention(config: ObservabilityConfig) -> None:
    logs_dir = config.output_dir / "logs"
    profiles_dir = config.output_dir / "profiles"
    enforce_file_retention(
        logs_dir,
        max_age_seconds=config.log_retention_days * 24 * 60 * 60,
        max_total_bytes=config.log_max_bytes,
    )
    enforce_profile_retention(profiles_dir, config)


def enforce_file_retention(
    root: Path, *, max_age_seconds: int, max_total_bytes: int
) -> None:
    if not root.exists():
        return
    now = time.time()
    files = [path for path in root.rglob("*") if path.is_file()]
    for path in files:
        try:
            if max_age_seconds == 0 or now - path.stat().st_mtime > max_age_seconds:
                path.unlink()
        except OSError:
            continue

    remaining = sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda item: safe_mtime(item),
        reverse=True,
    )
    used = 0
    for path in remaining:
        size = safe_size(path)
        used += size
        if used <= max_total_bytes:
            continue
        try:
            path.unlink()
        except OSError:
            continue


def enforce_profile_retention(
    profiles_dir: Path, config: ObservabilityConfig
) -> None:
    if not profiles_dir.exists():
        return
    now = time.time()
    regular: list[Path] = []
    content: list[Path] = []
    for directory in profiles_dir.iterdir():
        if not directory.is_dir():
            continue
        target = (
            content
            if any(directory.glob("content-*.jsonl"))
            else regular
        )
        target.append(directory)

    prune_profile_group(
        profiles_dir,
        regular,
        now=now,
        max_age_seconds=config.profile_retention_days * 24 * 60 * 60,
        max_total_bytes=config.profile_max_bytes,
    )
    prune_profile_group(
        profiles_dir,
        content,
        now=now,
        max_age_seconds=config.content_profile_retention_hours * 60 * 60,
        max_total_bytes=config.content_profile_max_bytes,
    )


def prune_profile_group(
    parent: Path,
    directories: list[Path],
    *,
    now: float,
    max_age_seconds: int,
    max_total_bytes: int,
) -> None:
    survivors: list[Path] = []
    for directory in directories:
        age = now - safe_mtime(directory)
        if max_age_seconds == 0 or age > max_age_seconds:
            safe_remove_directory(parent, directory)
        else:
            survivors.append(directory)

    used = 0
    for directory in sorted(
        survivors,
        key=safe_mtime,
        reverse=True,
    ):
        used += directory_size(directory)
        if used <= max_total_bytes:
            continue
        safe_remove_directory(parent, directory)


def safe_remove_directory(parent: Path, target: Path) -> None:
    try:
        resolved_parent = parent.resolve()
        resolved_target = target.resolve()
        resolved_target.relative_to(resolved_parent)
    except (OSError, ValueError):
        return
    if resolved_target == resolved_parent:
        return
    try:
        shutil.rmtree(resolved_target)
    except OSError:
        return


def directory_size(directory: Path) -> int:
    return sum(
        safe_size(path)
        for path in directory.rglob("*")
        if path.is_file()
    )


def safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0
