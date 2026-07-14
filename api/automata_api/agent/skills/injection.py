from __future__ import annotations

import re
from pathlib import Path

from automata_api.agent.skills.model import (
    AgentMode,
    SkillLoadOutcome,
    SkillMetadata,
    SkillSelection,
)


MENTION_RE = re.compile(r"(?<![A-Za-z0-9_\\/-])\$([A-Za-z0-9_:-]+)")


def collect_skill_mentions(prompt: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(match.group(1) for match in MENTION_RE.finditer(prompt)))


def resolve_selected_skills(
    *,
    outcome: SkillLoadOutcome,
    prompt: str,
    selections: tuple[SkillSelection, ...],
    mode: AgentMode,
) -> tuple[tuple[SkillMetadata, ...], tuple[str, ...]]:
    enabled = outcome.enabled_skills(mode=mode)
    warnings: list[str] = []
    selected: list[SkillMetadata] = []
    seen_paths: set[Path] = set()

    for selection in selections:
        skill = resolve_structured_selection(enabled, selection)
        if skill is None:
            warnings.append(skill_selection_warning(selection))
            continue
        if skill.path not in seen_paths:
            selected.append(skill)
            seen_paths.add(skill.path)

    for name in collect_skill_mentions(prompt):
        if any(skill.name == name for skill in selected):
            continue
        matches = [skill for skill in enabled if skill.name == name]
        if len(matches) == 1:
            skill = matches[0]
            if skill.path not in seen_paths:
                selected.append(skill)
                seen_paths.add(skill.path)
            continue
        if len(matches) > 1:
            warnings.append(f"Skill name is ambiguous: {name}")
        else:
            warnings.append(f"Skill not found or disabled for this mode: {name}")

    return tuple(selected), tuple(warnings)


def build_skill_messages(
    skills: tuple[SkillMetadata, ...],
    *,
    body_budget_chars: int,
) -> tuple[tuple[dict[str, str], ...], tuple[str, ...]]:
    messages: list[dict[str, str]] = []
    warnings: list[str] = []
    for skill in skills:
        try:
            contents = skill.path.read_text(encoding="utf-8")
        except OSError as error:
            warnings.append(f"Failed to read skill {skill.name}: {error}")
            continue
        if len(contents) > body_budget_chars:
            warnings.append(
                f"Skill {skill.name} exceeds maximum body length of {body_budget_chars} characters."
            )
            continue
        messages.append(
            {
                "role": "user",
                "content": (
                    "<skill>\n"
                    f"<name>{skill.name}</name>\n"
                    f"<path>{str(skill.path).replace('\\', '/')}</path>\n"
                    f"{contents}\n"
                    "</skill>"
                ),
            }
        )
    return tuple(messages), tuple(warnings)


def resolve_structured_selection(
    skills: tuple[SkillMetadata, ...], selection: SkillSelection
) -> SkillMetadata | None:
    if selection.path is not None:
        path = selection.path.expanduser().resolve()
        return next((skill for skill in skills if skill.path == path), None)
    if selection.name:
        matches = [skill for skill in skills if skill.name == selection.name]
        return matches[0] if len(matches) == 1 else None
    return None


def skill_selection_warning(selection: SkillSelection) -> str:
    if selection.path is not None:
        return f"Skill selection path is not available: {selection.path}"
    if selection.name:
        return f"Skill selection is not available or is ambiguous: {selection.name}"
    return "Skill selection is missing name and path."
