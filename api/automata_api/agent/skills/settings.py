from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from automata_api.config import get_database_config

from .model import SkillMetadata


SETTINGS_VERSION = 2


@dataclass(frozen=True)
class DisabledSkillRule:
    skill_id: str
    scope: str
    root_id: str
    relative_dir: str
    name: str
    path_hint: str
    fingerprint: str

    @classmethod
    def from_skill(cls, skill: SkillMetadata) -> "DisabledSkillRule":
        return cls(
            skill_id=skill.skill_id,
            scope=skill.scope,
            root_id=skill.root_id,
            relative_dir=skill.relative_dir,
            name=skill.name,
            path_hint=str(skill.path),
            fingerprint=skill.fingerprint,
        )

    @classmethod
    def from_json(cls, value: Any) -> "DisabledSkillRule | None":
        if not isinstance(value, dict):
            return None
        skill_id = value.get("skill_id")
        if not isinstance(skill_id, str) or not skill_id:
            return None
        return cls(
            skill_id=skill_id,
            scope=_text(value.get("scope")),
            root_id=_text(value.get("root_id")),
            relative_dir=_text(value.get("relative_dir")),
            name=_text(value.get("name")),
            path_hint=_text(value.get("path_hint")),
            fingerprint=_text(value.get("fingerprint")),
        )

    def to_json(self) -> dict[str, str]:
        return {
            "skill_id": self.skill_id,
            "scope": self.scope,
            "root_id": self.root_id,
            "relative_dir": self.relative_dir,
            "name": self.name,
            "path_hint": self.path_hint,
            "fingerprint": self.fingerprint,
        }


class SkillSettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (get_database_config().path.parent / "skills-config.json")
        self._lock = threading.Lock()
        self._revision = 0

    @property
    def revision(self) -> int:
        return self._revision

    def disabled_rules(self) -> tuple[DisabledSkillRule, ...]:
        with self._lock:
            return self._read_locked()

    def disabled_skill_ids(self) -> frozenset[str]:
        return frozenset(rule.skill_id for rule in self.disabled_rules())

    def set_enabled(self, skill: SkillMetadata, *, enabled: bool) -> None:
        with self._lock:
            current = {
                rule.skill_id: rule
                for rule in self._read_locked()
            }
            if enabled:
                current.pop(skill.skill_id, None)
            else:
                current[skill.skill_id] = DisabledSkillRule.from_skill(skill)
            self._write_locked(tuple(current.values()))
            self._revision += 1

    def _read_locked(self) -> tuple[DisabledSkillRule, ...]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ()
        except (OSError, json.JSONDecodeError):
            return ()
        if not isinstance(payload, dict) or payload.get("version") != SETTINGS_VERSION:
            return ()
        raw_rules = payload.get("disabled")
        if not isinstance(raw_rules, list):
            return ()
        return tuple(
            rule
            for item in raw_rules
            if (rule := DisabledSkillRule.from_json(item)) is not None
        )

    def _write_locked(self, rules: tuple[DisabledSkillRule, ...]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SETTINGS_VERSION,
            "disabled": [
                rule.to_json()
                for rule in sorted(rules, key=lambda item: item.skill_id)
            ],
        }
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""
