from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from automata_api.config import api_dir, get_database_config, read_bool_env, read_int_env


DEFAULT_SKILL_METADATA_BUDGET_CHARS = 8_000
DEFAULT_SKILL_BODY_BUDGET_CHARS = 65_536


@dataclass(frozen=True)
class SkillsConfig:
    enabled: bool
    packaged_enabled: bool
    metadata_budget_chars: int
    body_budget_chars: int
    user_root: Path
    packaged_root: Path
    extra_roots: tuple[Path, ...]


def get_skills_config() -> SkillsConfig:
    data_dir = get_database_config().path.parent
    extra_roots = tuple(
        Path(value).expanduser()
        for value in split_path_env(os.environ.get("AUTOMATA_SKILL_ROOTS", ""))
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
        extra_roots=extra_roots,
    )


def split_path_env(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(os.pathsep) if item.strip())
