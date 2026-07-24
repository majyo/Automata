from __future__ import annotations

from automata_api.agent.mcp.config import load_mcp_config
from automata_api.agent.mcp.trust import McpTrustStore
from automata_api.agent.skills.model import (
    SkillDependencyDiagnostic,
    SkillMetadata,
    SkillToolDependency,
)
from automata_api.agent.tools.model import ToolDescriptor, ToolExposure
from automata_api.agent.tools.router import ToolRouter
from automata_api.agent.tools.tool_search import search_tool_descriptors


def diagnose_skill_dependencies(
    skill: SkillMetadata,
    *,
    router: ToolRouter | None,
    workspace: str,
) -> tuple[SkillDependencyDiagnostic, ...]:
    dependencies = skill.dependencies
    if dependencies is None:
        return ()
    return tuple(
        diagnose_dependency(dependency, router=router, workspace=workspace)
        for dependency in dependencies.tools
    )


def diagnose_dependency(
    dependency: SkillToolDependency,
    *,
    router: ToolRouter | None,
    workspace: str,
) -> SkillDependencyDiagnostic:
    dependency_type = dependency.type.lower().strip()
    descriptors = router.descriptors() if router is not None else ()

    if dependency_type == "builtin":
        return diagnose_builtin(dependency, descriptors)
    if dependency_type in {"deferred", "tool_search"}:
        return diagnose_deferred(dependency, descriptors)
    if dependency_type == "mcp":
        return diagnose_mcp(dependency, descriptors, workspace)
    return diagnostic(
        dependency,
        "unknown",
        f"Unknown dependency type: {dependency.type}",
    )


def diagnose_builtin(
    dependency: SkillToolDependency,
    descriptors: tuple[ToolDescriptor, ...],
) -> SkillDependencyDiagnostic:
    name = dependency.value or ""
    descriptor = next((item for item in descriptors if item.name == name), None)
    if descriptor is None:
        return diagnostic(dependency, "not_found", f"Tool is not registered: {name}")
    return descriptor_diagnostic(dependency, descriptor)


def diagnose_deferred(
    dependency: SkillToolDependency,
    descriptors: tuple[ToolDescriptor, ...],
) -> SkillDependencyDiagnostic:
    if dependency.value:
        matches = [
            descriptor
            for descriptor in descriptors
            if descriptor.name == dependency.value
        ]
    elif dependency.query:
        matches = search_tool_descriptors(
            dependency.query,
            list(descriptors),
            1,
        )
    else:
        matches = []
    if not matches:
        label = dependency.value or dependency.query or "(missing query)"
        return diagnostic(
            dependency,
            "not_found",
            f"No matching deferred tool was found: {label}",
        )
    return descriptor_diagnostic(dependency, matches[0])


def diagnose_mcp(
    dependency: SkillToolDependency,
    descriptors: tuple[ToolDescriptor, ...],
    workspace: str,
) -> SkillDependencyDiagnostic:
    server = dependency.server or ""
    tool = dependency.tool or ""
    candidates = [
        descriptor
        for descriptor in descriptors
        if descriptor.source == f"mcp:{server}"
        and (
            not tool
            or descriptor.identity == f"mcp:{server}:{tool}"
            or descriptor.identity is not None
            and descriptor.identity.endswith(f":{tool}")
            or descriptor.name.endswith(f"__{tool}")
        )
    ]
    if candidates:
        return descriptor_diagnostic(dependency, candidates[0])

    config = load_mcp_config(workspace)
    definition = next(
        (candidate for candidate in config.definitions if candidate.name == server),
        None,
    )
    if definition is None:
        return diagnostic(
            dependency,
            "not_found",
            f"MCP server is not configured: {server}",
        )
    grant = McpTrustStore().grant_for(definition, workspace)
    if grant is None or grant.connection != "allow":
        return diagnostic(
            dependency,
            "not_granted",
            f"MCP server is configured but not granted: {server}",
        )
    return diagnostic(
        dependency,
        "deferred",
        f"MCP server is granted; tool availability is verified when the run starts: {server}/{tool}",
    )


def descriptor_diagnostic(
    dependency: SkillToolDependency,
    descriptor: ToolDescriptor,
) -> SkillDependencyDiagnostic:
    if descriptor.exposure == ToolExposure.HIDDEN:
        return diagnostic(
            dependency,
            "not_found",
            f"Tool is registered but hidden: {descriptor.name}",
        )
    if descriptor.exposure == ToolExposure.DEFERRED:
        return diagnostic(
            dependency,
            "deferred",
            f"Tool is available through tool_search: {descriptor.name}",
        )
    return diagnostic(
        dependency,
        "available",
        f"Tool is available: {descriptor.name}",
    )


def diagnostic(
    dependency: SkillToolDependency,
    status,
    message: str,
) -> SkillDependencyDiagnostic:
    return SkillDependencyDiagnostic(
        dependency_type=dependency.type,
        status=status,
        message=message,
        value=dependency.value,
        query=dependency.query,
        server=dependency.server,
        tool=dependency.tool,
    )
