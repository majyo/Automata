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

from automata_api.agent.execution.coordinator import RunCoordinator
from automata_api.agent.execution.event_hub import RunEventHub
from automata_api.agent.execution.process import ProcessSupervisor
from automata_api.agent.execution.process_sessions import ProcessSessionManager
from automata_api.bootstrap.settings import AppSettings, load_settings


@dataclass
class AppContainer:
    """The application scoped resource graph."""

    settings: AppSettings
    event_hub: RunEventHub = field(default_factory=RunEventHub)
    process_supervisor: ProcessSupervisor = field(default_factory=ProcessSupervisor)
    process_sessions: ProcessSessionManager = field(
        default_factory=ProcessSessionManager
    )
    _coordinator: RunCoordinator | None = field(default=None, repr=False)

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
                run_event_retention_days=self.settings.run_events.retention_days,
            )
        return self._coordinator


def create_container(settings: AppSettings | None = None) -> AppContainer:
    """Build a container for one application instance."""
    return AppContainer(settings=settings or load_settings())
