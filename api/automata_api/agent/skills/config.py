from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from automata_api.config import api_dir, get_database_config, read_bool_env, read_int_env


DEFAULT_SKILL_METADATA_BUDGET_CHARS = 8_000
DEFAULT_SKILL_BODY_BUDGET_CHARS = 65_536
DEFAULT_SKILL_CACHE_TTL_SECONDS = 2.0


@dataclass(frozen=True)
class SkillsConfig:
    enabled: bool
    packaged_enabled: bool
    metadata_budget_chars: int
    body_budget_chars: int
    user_root: Path
    packaged_root: Path
    extra_roots: tuple[Path, ...]
    cache_ttl_seconds: float = DEFAULT_SKILL_CACHE_TTL_SECONDS
    extra_root_ids: tuple[str, ...] = ()


def get_skills_config() -> SkillsConfig:
    data_dir = get_database_config().path.parent
    configured_extra_roots = tuple(
        parse_extra_root(value, index)
        for index, value in enumerate(
            split_path_env(os.environ.get("AUTOMATA_SKILL_ROOTS", ""))
        )
    )
    return SkillsConfig(
        enabled=read_bool_env("AUTOMATA_SKILLS_ENABLED", True),
        packaged_enabled=read_bool_env("AUTOMATA_SYSTEM_SKILLS_ENABLED", True),
        metadata_budget_chars=read_int_env(
            "AUTOMATA_SKILL_METADATA_BUDGET_CHARS",
            DEFAULT_SKILL_METADATA_BUDGET_CHARS,
        ),
        body_budget_chars=read_int_env(
            "AUTOMATA_SKILL_BODY_BUDGET_CHARS",
            DEFAULT_SKILL_BODY_BUDGET_CHARS,
        ),
        user_root=data_dir / "skills",
        packaged_root=api_dir() / "automata_api" / "skills" / ".system",
        extra_roots=tuple(path for _, path in configured_extra_roots),
        cache_ttl_seconds=read_float_env(
            "AUTOMATA_SKILL_CACHE_TTL_SECONDS",
            DEFAULT_SKILL_CACHE_TTL_SECONDS,
        ),
        extra_root_ids=tuple(root_id for root_id, _ in configured_extra_roots),
    )


def split_path_env(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(os.pathsep) if item.strip())


def read_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def parse_extra_root(value: str, index: int) -> tuple[str, Path]:
    if "=" in value:
        candidate_id, candidate_path = value.split("=", 1)
        root_id = candidate_id.strip()
        path_text = candidate_path.strip()
        if root_id and path_text:
            return root_id, Path(path_text).expanduser()
    return f"extra-{index}", Path(value).expanduser()
