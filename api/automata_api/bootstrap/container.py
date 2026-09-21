"""Application scoped dependency container.

``AppContainer`` holds exactly the resources whose lifetime is the
application instance: the run coordinator, the event hub, the process
registries and the configuration snapshot. Per-Run resources (cancellation
tokens, approval brokers, event sinks, tool catalogues) are created by
``bootstrap.turn_resources`` and never stored here, so two app instances in
one process cannot share mutable run state.

Nothing in the business layers may import this module; the container is
passed to ``create_app`` and read from ``app.state``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from automata_api.bootstrap.settings import AppSettings, load_settings
from automata_api.bootstrap.turn_resources import DefaultTurnResourcesFactory
from automata_api.core.agent.ports import ModelProvider
from automata_api.core.agent.resources import TurnResourcesFactory
from automata_api.core.runs.coordinator import RunCoordinator
from automata_api.core.runs.event_hub import RunEventHub
from automata_api.core.runs.ports import RunStore
from automata_api.core.runs.replay import ReplayService
from automata_api.core.runs.service import RunService
from automata_api.core.runs.turns import TurnService
from automata_api.core.sessions.ports import (
    ContextStore,
    ConversationStore,
    SessionStore,
)
from automata_api.core.telemetry import configure_observer
from automata_api.core.tools.management import (
    McpCatalog,
    SandboxAdministration,
    SkillCatalog,
)
from automata_api.infrastructure.extensions.catalog import (
    LocalMcpCatalog,
    LocalSkillCatalog,
)
from automata_api.infrastructure.llm.chat_completions import ChatCompletionsProvider
from automata_api.infrastructure.observability.adapter import StructuredObserver
from automata_api.infrastructure.persistence.runs import SqliteRunStore
from automata_api.infrastructure.persistence.stores import (
    SqliteContextStore,
    SqliteConversationStore,
    SqliteSessionStore,
)
from automata_api.infrastructure.processes.process import ProcessSupervisor
from automata_api.infrastructure.processes.process_sessions import ProcessSessionManager
from automata_api.infrastructure.sandbox.administration import (
    LocalSandboxAdministration,
)


@dataclass
class AppContainer:
    """The application scoped resource graph.

    Stores are held here so that the *choice* of storage implementation is
    made once, at assembly time, instead of being re-decided by every
    caller. Per-Run resources are deliberately absent: they are created by
    ``bootstrap.turn_resources`` for one Run and never shared.
    """

    settings: AppSettings
    mcp: McpCatalog = field(default_factory=LocalMcpCatalog)
    skills: SkillCatalog = field(default_factory=LocalSkillCatalog)
    sandbox: SandboxAdministration = field(default_factory=LocalSandboxAdministration)
    event_hub: RunEventHub = field(default_factory=RunEventHub)
    process_supervisor: ProcessSupervisor = field(default_factory=ProcessSupervisor)
    process_sessions: ProcessSessionManager = field(
        default_factory=ProcessSessionManager
    )
    run_store: RunStore = field(default_factory=SqliteRunStore)
    session_store: SessionStore = field(default_factory=SqliteSessionStore)
    conversation_store: ConversationStore = field(
        default_factory=SqliteConversationStore
    )
    context_store: ContextStore = field(default_factory=SqliteContextStore)
    model_provider: ModelProvider = field(default_factory=ChatCompletionsProvider)
    turn_resources: TurnResourcesFactory | None = None
    _runs: RunService | None = field(default=None, repr=False)
    _coordinator: RunCoordinator | None = field(default=None, repr=False)
    _replay: ReplayService | None = field(default=None, repr=False)

    @property
    def runs(self) -> RunService:
        if self._runs is None:
            resources = self.turn_resources or DefaultTurnResourcesFactory(
                context=self.context_store, provider=self.model_provider
            )
            turns = TurnService(
                resources=resources,
                sessions=self.session_store,
                conversation=self.conversation_store,
                context=self.context_store,
            )
            self._runs = RunService(
                coordinator=self.coordinator,
                turns=turns,
                conversation=self.conversation_store,
                store=self.run_store,
            )
        return self._runs

    @property
    def replay(self) -> ReplayService:
        """Event replay for reconnecting connections."""
        if self._replay is None:
            self._replay = ReplayService(store=self.run_store)
        return self._replay

    @property
    def coordinator(self) -> RunCoordinator:
        """The run coordinator, built lazily against this container.

        Lazy construction keeps import order free: the coordinator needs
        the event hub, both process registries and the run store, all of
        which belong to this same container.
        """
        if self._coordinator is None:
            self._coordinator = RunCoordinator(
                event_hub=self.event_hub,
                process_supervisor=self.process_supervisor,
                process_sessions=self.process_sessions,
                store=self.run_store,
                run_event_retention_days=self.settings.run_events.retention_days,
            )
        return self._coordinator


def create_container(settings: AppSettings | None = None) -> AppContainer:
    """Build a container for one application instance."""
    configure_observer(StructuredObserver())
    return AppContainer(settings=settings or load_settings())
