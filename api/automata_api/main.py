"""Backend entry point and FastAPI application factory.

``create_app`` is a thin compatibility entry point: it builds (or accepts)
an :class:`~automata_api.bootstrap.container.AppContainer` and delegates
all assembly to ``bootstrap``. Transport code reads its services from
``app.state`` via the dependency helpers in ``transport.dependencies``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from automata_api.bootstrap.container import AppContainer, create_container
from automata_api.bootstrap.lifecycle import app_lifespan
from automata_api.bootstrap.settings import AppSettings
from automata_api.routers import chat, health, mcp, runs, sandbox, sessions, skills
from automata_api.security import bearer_token, token_is_valid


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
    config = resolved.settings.api

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async with app_lifespan(resolved):
            yield

    app = FastAPI(title="Automata Agent API", lifespan=lifespan)
    app.state.container = resolved

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def authenticate_http(request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path == "/health":
            return await call_next(request)
        candidate = bearer_token(request.headers.get("authorization"))
        if not token_is_valid(candidate):
            return JSONResponse(
                status_code=401,
                content={"detail": "API authentication required"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)

    app.include_router(health.router)
    app.include_router(sessions.router)
    app.include_router(mcp.router)
    app.include_router(skills.router)
    app.include_router(runs.router)
    app.include_router(sandbox.router)
    app.include_router(chat.router)
    return app


app = create_app()
