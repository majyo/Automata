from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from automata_api.agent.skills.config import SkillsConfig, get_skills_config
from automata_api.agent.skills.loader import load_skills_from_roots
from automata_api.agent.skills.model import SkillLoadOutcome, SkillRoot


@dataclass(frozen=True)
class SkillCacheKey:
    workspace: Path
    roots: tuple[tuple[str, str], ...]


class SkillManager:
    def __init__(self, config: SkillsConfig | None = None) -> None:
        self._config = config or get_skills_config()
        self._cache: dict[SkillCacheKey, SkillLoadOutcome] = {}

    def skills_for_workspace(
        self, workspace: str | Path, *, force_reload: bool = False
    ) -> SkillLoadOutcome:
        if not self._config.enabled:
            return SkillLoadOutcome()
        workspace_path = Path(workspace).expanduser().resolve()
        roots = self.skill_roots(workspace_path)
        key = SkillCacheKey(
            workspace=workspace_path,
            roots=tuple((str(root.path), root.scope) for root in roots),
        )
        if not force_reload and key in self._cache:
            return self._cache[key]
        outcome = load_skills_from_roots(
            roots,
            body_budget_chars=self._config.body_budget_chars,
        )
        self._cache[key] = outcome
        return outcome

    def skill_roots(self, workspace: Path) -> tuple[SkillRoot, ...]:
        roots: list[SkillRoot] = []
        for directory in dirs_between_project_root_and_workspace(workspace):
            roots.append(SkillRoot(directory / ".automata" / "skills", "repo"))
        roots.append(SkillRoot(self._config.user_root, "user"))
        if self._config.packaged_enabled:
            roots.append(SkillRoot(self._config.packaged_root, "packaged"))
        roots.extend(SkillRoot(path, "extra") for path in self._config.extra_roots)
        return tuple(dedupe_roots(roots))

    def clear_cache(self) -> None:
        self._cache.clear()


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
        result.append(SkillRoot(key[0], root.scope))
    return tuple(result)
