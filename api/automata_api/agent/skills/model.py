from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


SkillScope = Literal["repo", "user", "packaged", "extra", "plugin"]
AgentMode = Literal["act", "plan"]


@dataclass(frozen=True)
class SkillRoot:
    path: Path
    scope: SkillScope


@dataclass(frozen=True)
class SkillInterface:
    display_name: str | None = None
    short_description: str | None = None
    icon_small: Path | None = None
    icon_large: Path | None = None
    brand_color: str | None = None
    default_prompt: str | None = None


@dataclass(frozen=True)
class SkillToolDependency:
    type: str
    value: str | None = None
    description: str | None = None
    query: str | None = None
    server: str | None = None
    tool: str | None = None
    read_only: bool | None = None


@dataclass(frozen=True)
class SkillDependencies:
    tools: tuple[SkillToolDependency, ...] = ()


@dataclass(frozen=True)
class SkillPolicy:
    allow_implicit_invocation: bool = True
    modes: tuple[AgentMode, ...] = ("act", "plan")


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    short_description: str | None
    path: Path
    scope: SkillScope
    interface: SkillInterface | None = None
    dependencies: SkillDependencies | None = None
    policy: SkillPolicy = field(default_factory=SkillPolicy)


@dataclass(frozen=True)
class SkillError:
    path: Path
    message: str


@dataclass(frozen=True)
class SkillLoadOutcome:
    skills: tuple[SkillMetadata, ...] = ()
    errors: tuple[SkillError, ...] = ()
    disabled_paths: frozenset[Path] = frozenset()

    def enabled_skills(self, *, mode: AgentMode | None = None) -> tuple[SkillMetadata, ...]:
        return tuple(
            skill
            for skill in self.skills
            if skill.path not in self.disabled_paths
            and (mode is None or mode in skill.policy.modes)
        )


@dataclass(frozen=True)
class SkillSelection:
    name: str | None = None
    path: Path | None = None


@dataclass(frozen=True)
class SkillTurnContext:
    available_notes: str = ""
    injected_messages: tuple[dict[str, Any], ...] = ()
    selected: tuple[SkillMetadata, ...] = ()
    warnings: tuple[str, ...] = ()
    loaded_count: int = 0
    enabled_count: int = 0
