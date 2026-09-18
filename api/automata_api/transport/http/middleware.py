"""HTTP middleware for the API surface.

CORS and bearer authentication are transport concerns, so they live here
rather than in the application factory. The rules are unchanged: the health
endpoint and CORS preflight are exempt from authentication, and every other
route requires the bearer token.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from automata_api.bootstrap.settings import ApiConfig
from automata_api.security import bearer_token, token_is_valid

# Paths that never require the bearer token. ``/health`` is used by the
# desktop shell to decide whether the sidecar is up, before it has a token
# to present.
PUBLIC_PATHS = frozenset({"/health"})

ALLOWED_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
ALLOWED_HEADERS = ["Authorization", "Content-Type"]
UNAUTHENTICATED_DETAIL = "API authentication required"


def install_http_middleware(app: FastAPI, config: ApiConfig) -> None:
    """Attach CORS and authentication to ``app``.

    Order matters only in that both are registered before the routers are
    included; Starlette applies middleware outermost-last.
    """
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.cors_origins),
        allow_credentials=False,
        allow_methods=ALLOWED_METHODS,
        allow_headers=ALLOWED_HEADERS,
    )

    @app.middleware("http")
    async def authenticate_http(request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path in PUBLIC_PATHS:
            return await call_next(request)
        candidate = bearer_token(request.headers.get("authorization"))
        if not token_is_valid(candidate):
            return JSONResponse(
                status_code=401,
                content={"detail": UNAUTHENTICATED_DETAIL},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)
