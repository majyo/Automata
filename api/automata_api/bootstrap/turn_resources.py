"""Create and close backend, model and extension resources for one turn."""

from contextlib import asynccontextmanager

from automata_api.config import get_agent_config, get_context_compression_config
from automata_api.core.agent.ports import ModelProvider
from automata_api.core.agent.resources import TurnResources
from automata_api.core.agent.settings import TurnSettings
from automata_api.core.runs.model import PublicRunError
from automata_api.core.sessions.ports import ContextStore
from automata_api.infrastructure.extensions.mcp.lookup import mcp_config_lookup
from automata_api.infrastructure.extensions.mcp.runtime import create_mcp_tool_runtime
from automata_api.infrastructure.extensions.skills.runtime import (
    create_skill_turn_context,
    skill_selections_from_payload,
)
from automata_api.infrastructure.workspace.backends.factory import (
    BackendConfigurationError,
    create_backend,
)


class DefaultTurnResourcesFactory:
    def __init__(self, *, context: ContextStore, provider: ModelProvider) -> None:
        self.context = context
        self.provider = provider

    @asynccontextmanager
    async def open(
        self,
        *,
        session_id,
        session_config,
        mode,
        prompt,
        selected_skills,
        run_id,
        permission_profile,
        sender,
    ):
        config = get_agent_config()
        settings = TurnSettings(
            model=config.model,
            max_steps=config.max_steps,
            compression=get_context_compression_config(),
        )
        try:
            backend = create_backend(
                session_config["backend"], workspace=session_config["working_directory"]
            )
        except BackendConfigurationError as error:
            raise PublicRunError("backend_configuration_error", str(error)) from error
        async with backend:
            async with create_mcp_tool_runtime(
                backend=backend,
                session_id=session_id,
                workspace=session_config["working_directory"],
                mode=mode,
                permission_profile=permission_profile,
                run_id=run_id,
                emit_event=sender.send_json,
                context_store=self.context,
            ) as runtime:
                await send_mcp_runtime_events(sender, runtime, run_id)
                skills = await create_skill_turn_context(
                    workspace=session_config["working_directory"],
                    mode=mode,
                    prompt=prompt,
                    selected_skills=skill_selections_from_payload(selected_skills),
                    router=runtime.router,
                    mcp_lookup=mcp_config_lookup,
                )
                await send_skill_runtime_events(sender, skills, run_id)
                yield TurnResources(
                    workspace=session_config["working_directory"],
                    workspace_label=backend.workspace_label,
                    tool_notes=backend.prompt_notes(),
                    router=runtime.router,
                    skills=skills,
                    provider=self.provider,
                    settings=settings,
                )


async def send_mcp_runtime_events(websocket, runtime, run_id: str) -> None:
    for warning in runtime.warnings:
        await websocket.send_json(
            {
                "type": "mcp_server_status",
                "run_id": run_id,
                "status": "warning",
                "message": warning,
            }
        )
    for candidate in runtime.candidates:
        await websocket.send_json(
            {
                "type": "mcp_server_candidate",
                "run_id": run_id,
                "server": candidate.name,
                "provenance": candidate.provenance,
                "fingerprint": candidate.fingerprint,
            }
        )


async def send_skill_runtime_events(websocket, skill_context, run_id: str) -> None:
    if skill_context.loaded_count:
        await websocket.send_json(
            {
                "type": "skills_loaded",
                "run_id": run_id,
                "count": skill_context.loaded_count,
                "enabled_count": skill_context.enabled_count,
            }
        )
    for warning in skill_context.warnings:
        await websocket.send_json(
            {"type": "skills_warning", "run_id": run_id, "message": warning}
        )
    for skill in skill_context.selected:
        await websocket.send_json(
            {
                "type": "skill_injected",
                "run_id": run_id,
                "name": skill.name,
                "path": str(skill.path),
            }
        )
