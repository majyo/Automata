from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from automata_api.agent.skills.diagnostics import diagnose_skill_dependencies
from automata_api.agent.skills.manager import get_skill_manager
from automata_api.agent.skills.model import (
    SkillDependencyDiagnostic,
    SkillDependencies,
    SkillInterface,
    SkillLoadOutcome,
    SkillMetadata,
    SkillToolDependency,
)
from automata_api.agent.tools.router import ToolRouter
from automata_api.schemas import (
    SkillDependencyDiagnosticRecord,
    SkillDependenciesRecord,
    SkillDiagnosticsResponse,
    SkillEnabledRequest,
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
    outcome = get_skill_manager().skills_for_workspace(
        normalized_workspace,
        force_reload=force_reload,
    )
    diagnostic_router = ToolRouter.default_for_workspace(normalized_workspace)
    return skills_list_response(
        normalized_workspace,
        outcome,
        diagnostic_router=diagnostic_router,
    )


@router.put("/skills/{skill_id}/enabled", response_model=SkillRecord)
async def set_skill_enabled(
    skill_id: str,
    request: SkillEnabledRequest,
) -> SkillRecord:
    workspace = normalize_workspace(request.workspace)
    manager = get_skill_manager()
    try:
        manager.set_enabled(workspace, skill_id, enabled=request.enabled)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Skill was not found") from error
    outcome = manager.skills_for_workspace(workspace)
    skill = next(item for item in outcome.skills if item.skill_id == skill_id)
    return skill_record(
        skill,
        enabled=skill.skill_id not in outcome.disabled_skill_ids,
        workspace=workspace,
        diagnostic_router=ToolRouter.default_for_workspace(workspace),
    )


@router.get(
    "/skills/{skill_id}/diagnostics",
    response_model=SkillDiagnosticsResponse,
)
async def get_skill_diagnostics(
    skill_id: str,
    workspace: str,
) -> SkillDiagnosticsResponse:
    normalized_workspace = normalize_workspace(workspace)
    outcome = get_skill_manager().skills_for_workspace(normalized_workspace)
    skill = next(
        (candidate for candidate in outcome.skills if candidate.skill_id == skill_id),
        None,
    )
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill was not found")
    diagnostics = diagnose_skill_dependencies(
        skill,
        router=ToolRouter.default_for_workspace(normalized_workspace),
        workspace=normalized_workspace,
    )
    return SkillDiagnosticsResponse(
        skill_id=skill.skill_id,
        diagnostics=[diagnostic_record(item) for item in diagnostics],
    )


def normalize_workspace(value: str) -> str:
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise HTTPException(status_code=422, detail="Workspace must be an existing directory")
    return str(path)


def skills_list_response(
    workspace: str,
    outcome: SkillLoadOutcome,
    *,
    diagnostic_router: ToolRouter | None = None,
) -> SkillsListResponse:
    return SkillsListResponse(
        workspace=workspace,
        skills=[
            skill_record(
                skill,
                enabled=skill.skill_id not in outcome.disabled_skill_ids,
                workspace=workspace,
                diagnostic_router=diagnostic_router,
            )
            for skill in outcome.skills
        ],
        errors=[
            SkillErrorRecord(
                path=str(error.path),
                message=error.message,
                severity=error.severity,
            )
            for error in outcome.errors
        ],
    )


def skill_record(
    skill: SkillMetadata,
    *,
    enabled: bool,
    workspace: str,
    diagnostic_router: ToolRouter | None,
) -> SkillRecord:
    diagnostics = diagnose_skill_dependencies(
        skill,
        router=diagnostic_router,
        workspace=workspace,
    )
    return SkillRecord(
        skill_id=skill.skill_id,
        name=skill.name,
        description=skill.description,
        short_description=skill.short_description,
        path=str(skill.path),
        scope=skill.scope,
        enabled=enabled,
        root_id=skill.root_id,
        relative_dir=skill.relative_dir,
        fingerprint=skill.fingerprint,
        interface=interface_record(skill.interface),
        dependencies=dependencies_record(skill.dependencies),
        diagnostics=[diagnostic_record(item) for item in diagnostics],
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


def diagnostic_record(
    diagnostic: SkillDependencyDiagnostic,
) -> SkillDependencyDiagnosticRecord:
    return SkillDependencyDiagnosticRecord(
        dependency_type=diagnostic.dependency_type,
        status=diagnostic.status,
        message=diagnostic.message,
        value=diagnostic.value,
        query=diagnostic.query,
        server=diagnostic.server,
        tool=diagnostic.tool,
    )
