"""Application lifecycle: start-up validation, resource scopes, shutdown.

Shutdown order is part of the contract and is enforced here rather than
being scattered across routers:

1. stop accepting new work (the coordinator stops taking runs)
2. interrupt and settle in-flight runs
3. terminate process sessions and child processes
4. drop subscriptions and clear event resources
5. close the observability writer

Resources acquired before a later start-up step fails are still released,
because the ``try`` block covers the acquisition sequence.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from automata_api.bootstrap.container import AppContainer
from automata_api.infrastructure.observability import (
    get_observability_manager,
    start_observability,
    stop_observability,
)
from automata_api.infrastructure.persistence.db.schema import init_db
from automata_api.transport.security import get_api_token, validate_loopback_host

logger = logging.getLogger(__name__)


@asynccontextmanager
async def app_lifespan(container: AppContainer) -> AsyncIterator[AppContainer]:
    """Run one application instance's start-up and shutdown."""
    await start_observability()
    observer = get_observability_manager()
    log_observability_mode(observer)

    started = False
    try:
        validate_loopback_host(container.settings.api.host)
        get_api_token()
        init_db()
        await container.coordinator.startup()
        started = True
        yield container
    finally:
        if started:
            await shutdown_container(container)
        await stop_observability()


async def shutdown_container(container: AppContainer) -> None:
    """Release the application scoped resources in the documented order."""
    await container.coordinator.shutdown()
    await container.process_sessions.terminate_all()
    await container.process_supervisor.terminate_all()
    await container.event_hub.clear()


def log_observability_mode(observer) -> None:
    logger.info(
        "Observability started mode=%s output_dir=%s capture_content=%s",
        observer.config.mode if observer.config else "disabled",
        observer.config.output_dir if observer.config else "",
        observer.capture_content,
    )
    if observer.capture_content:
        logger.warning(
            "Profile content capture is enabled; artifacts may contain "
            "sensitive workspace and conversation data."
        )
