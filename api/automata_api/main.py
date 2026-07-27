import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from automata_api.agent.execution.coordinator import run_coordinator
from automata_api.agent.execution.event_hub import run_event_hub
from automata_api.agent.execution.process import process_supervisor
from automata_api.agent.execution.process_sessions import process_session_manager
from automata_api.config import get_api_config, load_local_env
from automata_api.db.schema import init_db
from automata_api.observability import (
    get_observability_manager,
    start_observability,
    stop_observability,
)
from automata_api.routers import chat, health, mcp, runs, sessions, skills
from automata_api.security import (
    bearer_token,
    get_api_token,
    token_is_valid,
    validate_loopback_host,
)

load_local_env()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await start_observability()
    observer = get_observability_manager()
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
    try:
        config = get_api_config()
        validate_loopback_host(config.host)
        get_api_token()
        init_db()
        await run_coordinator.startup()
        try:
            yield
        finally:
            await run_coordinator.shutdown()
            await process_session_manager.terminate_all()
            await process_supervisor.terminate_all()
            await run_event_hub.clear()
    finally:
        await stop_observability()


def create_app() -> FastAPI:
    config = get_api_config()
    app = FastAPI(title="Automata Agent API", lifespan=lifespan)

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
    app.include_router(chat.router)
    return app


app = create_app()
