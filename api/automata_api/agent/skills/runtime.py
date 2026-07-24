from __future__ import annotations

from pathlib import Path

from automata_api.agent.skills.config import get_skills_config
from automata_api.agent.skills.diagnostics import diagnose_skill_dependencies
from automata_api.agent.skills.injection import (
    build_skill_messages,
    resolve_selected_skills,
)
from automata_api.agent.skills.manager import SkillManager, get_skill_manager
from automata_api.agent.skills.model import (
    AgentMode,
    SkillSelection,
    SkillTurnContext,
)
from automata_api.agent.skills.render import render_available_skills
from automata_api.agent.tools.router import ToolRouter


async def create_skill_turn_context(
    *,
    workspace: str,
    mode: AgentMode,
    prompt: str,
    selected_skills: tuple[SkillSelection, ...] = (),
    router: ToolRouter | None = None,
    manager: SkillManager | None = None,
    force_reload: bool = False,
) -> SkillTurnContext:
    config = get_skills_config()
    if not config.enabled:
        return SkillTurnContext()

    skill_manager = manager or get_skill_manager()
    outcome = skill_manager.skills_for_workspace(workspace, force_reload=force_reload)
    enabled = outcome.enabled_skills(mode=mode)
    available_notes, render_warnings = render_available_skills(
        enabled,
        budget_chars=config.metadata_budget_chars,
    )
    selected, selection_warnings = resolve_selected_skills(
        outcome=outcome,
        prompt=prompt,
        selections=selected_skills,
        mode=mode,
    )
    injected_messages, injection_warnings = build_skill_messages(
        selected,
        body_budget_chars=config.body_budget_chars,
    )
    dependency_warnings = tuple(
        f"Skill {skill.name}: {diagnostic.message}"
        for skill in selected
        for diagnostic in diagnose_skill_dependencies(
            skill,
            router=router,
            workspace=workspace,
        )
        if diagnostic.status not in {"available", "deferred"}
    )
    warnings = (
        tuple(error.message for error in outcome.errors)
        + render_warnings
        + selection_warnings
        + injection_warnings
        + dependency_warnings
    )
    return SkillTurnContext(
        available_notes=available_notes,
        injected_messages=injected_messages,
        selected=selected,
        warnings=warnings,
        loaded_count=len(outcome.skills),
        enabled_count=len(enabled),
    )


def skill_selections_from_payload(value: object) -> tuple[SkillSelection, ...]:
    if not isinstance(value, list):
        return ()
    selections: list[SkillSelection] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        raw_name = item.get("name")
        raw_path = item.get("path")
        name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else None
        path = (
            Path(raw_path).expanduser().resolve()
            if isinstance(raw_path, str) and raw_path.strip()
            else None
        )
        if name or path:
            selections.append(SkillSelection(name=name, path=path))
    return tuple(selections)
