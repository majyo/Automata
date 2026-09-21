"""Backend entry point and FastAPI application factory.

``create_app`` is a thin compatibility entry point: it builds (or accepts)
an :class:`~automata_api.bootstrap.container.AppContainer` and delegates
all assembly to ``bootstrap``. Transport code reads its services from
``app.state`` via the dependency helpers in ``transport.dependencies``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from automata_api.bootstrap.container import AppContainer, create_container
from automata_api.bootstrap.lifecycle import app_lifespan
from automata_api.bootstrap.settings import AppSettings
from automata_api.transport.http import health, mcp, runs, sandbox, sessions, skills
from automata_api.transport.http.middleware import install_http_middleware
from automata_api.transport.websocket import route as chat


def create_app(
    settings: AppSettings | None = None,
    container: AppContainer | None = None,
) -> FastAPI:
    """Build one application instance.

    ``settings`` and ``container`` exist so tests can inject a fully
    configured graph; the default path builds a container from the current
    environment exactly as before.
    """
    resolved = container or create_container(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async with app_lifespan(resolved):
            yield

    app = FastAPI(title="Automata Agent API", lifespan=lifespan)
    app.state.container = resolved

    install_http_middleware(app, resolved.settings.api)

    app.include_router(health.router)
    app.include_router(sessions.router)
    app.include_router(mcp.router)
    app.include_router(skills.router)
    app.include_router(runs.router)
    app.include_router(sandbox.router)
    app.include_router(chat.router)
    return app


app = create_app()
