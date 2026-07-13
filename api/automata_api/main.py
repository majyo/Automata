from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from automata_api.config import get_api_config, load_local_env
from automata_api.db.schema import init_db
from automata_api.routers import chat, health, mcp, sessions


load_local_env()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


def create_app() -> FastAPI:
    config = get_api_config()
    app = FastAPI(title="Automata Agent API", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(sessions.router)
    app.include_router(mcp.router)
    app.include_router(chat.router)
    return app


app = create_app()
