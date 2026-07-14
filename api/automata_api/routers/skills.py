from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from automata_api.agent.skills.manager import SkillManager
from automata_api.agent.skills.model import (
    SkillDependencies,
    SkillInterface,
    SkillLoadOutcome,
    SkillMetadata,
    SkillToolDependency,
)
from automata_api.schemas import (
    SkillDependenciesRecord,
    SkillErrorRecord,
    SkillInterfaceRecord,
    SkillRecord,
    SkillToolDependencyRecord,
    SkillsListResponse,
)


router = APIRouter()


@router.get("/skills", response_model=SkillsListResponse)
async def list_skills(workspace: str, force_reload: bool = False) -> SkillsListResponse:
    normalized_workspace = normalize_workspace(workspace)
    outcome = SkillManager().skills_for_workspace(
        normalized_workspace,
        force_reload=force_reload,
    )
    return skills_list_response(normalized_workspace, outcome)


def normalize_workspace(value: str) -> str:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise HTTPException(status_code=422, detail="Workspace must be an existing directory")
    return str(path)


def skills_list_response(workspace: str, outcome: SkillLoadOutcome) -> SkillsListResponse:
    return SkillsListResponse(
        workspace=workspace,
        skills=[
            skill_record(skill, enabled=skill.path not in outcome.disabled_paths)
            for skill in outcome.skills
        ],
        errors=[
            SkillErrorRecord(path=str(error.path), message=error.message)
            for error in outcome.errors
        ],
    )


def skill_record(skill: SkillMetadata, *, enabled: bool) -> SkillRecord:
    return SkillRecord(
        name=skill.name,
        description=skill.description,
        short_description=skill.short_description,
        path=str(skill.path),
        scope=skill.scope,
        enabled=enabled,
        interface=interface_record(skill.interface),
        dependencies=dependencies_record(skill.dependencies),
    )


def interface_record(interface: SkillInterface | None) -> SkillInterfaceRecord | None:
    if interface is None:
        return None
    return SkillInterfaceRecord(
        display_name=interface.display_name,
        short_description=interface.short_description,
        icon_small=str(interface.icon_small) if interface.icon_small else None,
        icon_large=str(interface.icon_large) if interface.icon_large else None,
        brand_color=interface.brand_color,
        default_prompt=interface.default_prompt,
    )


def dependencies_record(
    dependencies: SkillDependencies | None,
) -> SkillDependenciesRecord | None:
    if dependencies is None:
        return None
    return SkillDependenciesRecord(
        tools=[dependency_record(item) for item in dependencies.tools]
    )


def dependency_record(dependency: SkillToolDependency) -> SkillToolDependencyRecord:
    return SkillToolDependencyRecord(
        type=dependency.type,
        value=dependency.value,
        description=dependency.description,
        query=dependency.query,
        server=dependency.server,
        tool=dependency.tool,
        read_only=dependency.read_only,
    )
