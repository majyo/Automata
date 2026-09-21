"""Per-turn resources supplied by the composition root."""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, Protocol

from automata_api.core.agent.ports import ModelProvider
from automata_api.core.agent.settings import TurnSettings
from automata_api.core.agent.skills.model import SkillTurnContext
from automata_api.core.tools.permissions import CompiledPermissionProfile
from automata_api.core.tools.router import ToolRouter


class EventSender(Protocol):
    async def send_json(self, data: Any) -> None: ...


@dataclass(frozen=True)
class TurnResources:
    workspace: str
    workspace_label: str
    tool_notes: str
    router: ToolRouter
    skills: SkillTurnContext
    provider: ModelProvider
    settings: TurnSettings = field(default_factory=TurnSettings)


class TurnResourcesFactory(Protocol):
    def open(
        self,
        *,
        session_id: str,
        session_config: dict[str, str],
        mode: str,
        prompt: str,
        selected_skills: object,
        run_id: str,
        permission_profile: CompiledPermissionProfile | None,
        sender: EventSender,
    ) -> AbstractAsyncContextManager[TurnResources]: ...
