from __future__ import annotations

import time
from dataclasses import dataclass, replace
from pathlib import Path

from automata_api.agent.skills.config import SkillsConfig, get_skills_config
from automata_api.agent.skills.loader import (
    discover_skill_files,
    load_skills_from_roots,
)
from automata_api.agent.skills.model import SkillLoadOutcome, SkillMetadata, SkillRoot
from automata_api.agent.skills.settings import SkillSettingsStore


@dataclass(frozen=True)
class SkillCacheKey:
    workspace: Path
    roots: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class SkillCacheEntry:
    outcome: SkillLoadOutcome
    fingerprint: tuple[tuple[str, int, int], ...]
    checked_at: float


class SkillManager:
    def __init__(
        self,
        config: SkillsConfig | None = None,
        settings: SkillSettingsStore | None = None,
    ) -> None:
        self._config = config or get_skills_config()
        self._settings = settings or SkillSettingsStore()
        self._cache: dict[SkillCacheKey, SkillCacheEntry] = {}

    @property
    def config(self) -> SkillsConfig:
        return self._config

    def skills_for_workspace(
        self, workspace: str | Path, *, force_reload: bool = False
    ) -> SkillLoadOutcome:
        if not self._config.enabled:
            return SkillLoadOutcome()
        workspace_path = Path(workspace).expanduser().resolve()
        roots = self.skill_roots(workspace_path)
        key = SkillCacheKey(
            workspace=workspace_path,
            roots=tuple(
                (str(root.path), root.scope, root.root_id)
                for root in roots
            ),
        )
        now = time.monotonic()
        cached = self._cache.get(key)
        if (
            not force_reload
            and cached is not None
            and now - cached.checked_at < self._config.cache_ttl_seconds
        ):
            return self._apply_settings(cached.outcome)

        fingerprint = roots_fingerprint(roots)
        if (
            not force_reload
            and cached is not None
            and cached.fingerprint == fingerprint
        ):
            refreshed = replace(cached, checked_at=now)
            self._cache[key] = refreshed
            return self._apply_settings(refreshed.outcome)

        outcome = load_skills_from_roots(
            roots,
            body_budget_chars=self._config.body_budget_chars,
        )
        self._cache[key] = SkillCacheEntry(
            outcome=outcome,
            fingerprint=fingerprint,
            checked_at=now,
        )
        return self._apply_settings(outcome)

    def skill_roots(self, workspace: Path) -> tuple[SkillRoot, ...]:
        roots: list[SkillRoot] = []
        project_root = find_project_root(workspace)
        for directory in dirs_between_project_root_and_workspace(workspace):
            relative = directory.relative_to(project_root).as_posix()
            roots.append(
                SkillRoot(
                    directory / ".automata" / "skills",
                    "repo",
                    f"repo:{relative or '.'}",
                )
            )
        roots.append(SkillRoot(self._config.user_root, "user", "user-data"))
        if self._config.packaged_enabled:
            roots.append(
                SkillRoot(self._config.packaged_root, "packaged", "packaged")
            )
        roots.extend(
            SkillRoot(
                path,
                "extra",
                (
                    self._config.extra_root_ids[index]
                    if index < len(self._config.extra_root_ids)
                    else f"extra-{index}"
                ),
            )
            for index, path in enumerate(self._config.extra_roots)
        )
        return tuple(dedupe_roots(roots))

    def clear_cache(self) -> None:
        self._cache.clear()

    def set_enabled(
        self,
        workspace: str | Path,
        skill_id: str,
        *,
        enabled: bool,
    ) -> SkillMetadata:
        outcome = self.skills_for_workspace(workspace, force_reload=True)
        skill = next(
            (candidate for candidate in outcome.skills if candidate.skill_id == skill_id),
            None,
        )
        if skill is None:
            raise KeyError(skill_id)
        self._settings.set_enabled(skill, enabled=enabled)
        return skill

    def _apply_settings(self, outcome: SkillLoadOutcome) -> SkillLoadOutcome:
        disabled_ids = self._settings.disabled_skill_ids()
        disabled_paths = frozenset(
            skill.path
            for skill in outcome.skills
            if skill.skill_id in disabled_ids
        )
        return replace(
            outcome,
            disabled_paths=disabled_paths,
            disabled_skill_ids=disabled_ids,
        )


def dirs_between_project_root_and_workspace(workspace: Path) -> tuple[Path, ...]:
    workspace = workspace.resolve()
    project_root = find_project_root(workspace)
    dirs: list[Path] = []
    for candidate in (workspace, *workspace.parents):
        dirs.append(candidate)
        if candidate == project_root:
            break
    return tuple(reversed(dirs))


def find_project_root(workspace: Path) -> Path:
    markers = (".git", "pyproject.toml", "package.json", "uv.lock")
    for candidate in (workspace, *workspace.parents):
        if any((candidate / marker).exists() for marker in markers):
            return candidate
    return workspace


def dedupe_roots(roots: list[SkillRoot]) -> tuple[SkillRoot, ...]:
    result: list[SkillRoot] = []
    seen: set[tuple[Path, str]] = set()
    for root in roots:
        key = (root.path.expanduser().resolve(), root.scope)
        if key in seen:
            continue
        seen.add(key)
        result.append(SkillRoot(key[0], root.scope, root.root_id))
    return tuple(result)


def roots_fingerprint(
    roots: tuple[SkillRoot, ...],
) -> tuple[tuple[str, int, int], ...]:
    entries: list[tuple[str, int, int]] = []
    for root in roots:
        for skill_path in discover_skill_files(root.path):
            for candidate in (
                skill_path,
                skill_path.parent / "agents" / "openai.yaml",
            ):
                try:
                    stat = candidate.stat()
                except OSError:
                    continue
                entries.append(
                    (
                        str(candidate.resolve()),
                        stat.st_mtime_ns,
                        stat.st_size,
                    )
                )
    return tuple(sorted(entries))


_shared_manager: SkillManager | None = None


def get_skill_manager() -> SkillManager:
    global _shared_manager
    config = get_skills_config()
    if _shared_manager is None or _shared_manager.config != config:
        _shared_manager = SkillManager(config)
    return _shared_manager


def reset_skill_manager() -> None:
    global _shared_manager
    _shared_manager = None
