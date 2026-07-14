from __future__ import annotations

from pathlib import Path

from automata_api.agent.skills.config import DEFAULT_SKILL_METADATA_BUDGET_CHARS
from automata_api.agent.skills.model import SkillMetadata


def render_available_skills(
    skills: tuple[SkillMetadata, ...],
    *,
    budget_chars: int = DEFAULT_SKILL_METADATA_BUDGET_CHARS,
) -> tuple[str, tuple[str, ...]]:
    if not skills:
        return "", ()
    full_lines = [render_skill_line(skill, include_description=True) for skill in skills]
    body = render_body(full_lines)
    if len(body) <= budget_chars:
        return body, ()

    minimum_lines = [render_skill_line(skill, include_description=False) for skill in skills]
    body = render_body(minimum_lines)
    if len(body) <= budget_chars:
        return body, ("Skill descriptions were shortened to fit the skills context budget.",)

    kept: list[str] = []
    omitted = 0
    for line in minimum_lines:
        candidate = render_body([*kept, line])
        if len(candidate) <= budget_chars:
            kept.append(line)
        else:
            omitted += 1
    warning = (
        f"Exceeded skills context budget; {omitted} skills were omitted from the model-visible list."
        if omitted
        else "Exceeded skills context budget."
    )
    return render_body(kept), (warning,)


def render_body(skill_lines: list[str]) -> str:
    if not skill_lines:
        return ""
    lines = [
        "## Skills",
        "A skill is a local reusable instruction package stored in SKILL.md.",
        "",
        "### Available skills",
        *skill_lines,
        "",
        "### How to use skills",
        "- If the user names a skill with $skill-name or the task clearly matches a skill description, use it for this turn.",
        "- If a skill needs a deferred tool, use tool_search first.",
        "- A skill cannot grant MCP access, activate deferred tools, or bypass plan mode/tool policy.",
        "- Do not carry a skill into later turns unless it is re-mentioned or still clearly applies.",
    ]
    return "\n".join(lines)


def render_skill_line(skill: SkillMetadata, *, include_description: bool) -> str:
    path = normalized_path(skill.path)
    if include_description and skill.description:
        return f"- {skill.name}: {skill.description} (file: {path})"
    return f"- {skill.name}: (file: {path})"


def normalized_path(path: Path) -> str:
    return str(path).replace("\\", "/")
