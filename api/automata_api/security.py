import asyncio
import ipaddress
import os
import secrets
from collections.abc import Iterable

from fastapi import WebSocket, WebSocketDisconnect


API_TOKEN_ENV = "AUTOMATA_API_TOKEN"
MIN_API_TOKEN_CHARS = 32
WEBSOCKET_AUTH_TIMEOUT_SECONDS = 3.0


class ApiSecurityConfigurationError(RuntimeError):
    pass


def get_api_token() -> str:
    token = os.environ.get(API_TOKEN_ENV, "").strip()
    if len(token) < MIN_API_TOKEN_CHARS:
        raise ApiSecurityConfigurationError(
            f"{API_TOKEN_ENV} must contain at least {MIN_API_TOKEN_CHARS} characters."
        )
    return token


def validate_loopback_host(host: str) -> None:
    normalized = host.strip().lower().strip("[]")
    if normalized == "localhost":
        return
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError as error:
        raise ApiSecurityConfigurationError(
            f"AUTOMATA_API_HOST must be a loopback address, got: {host}"
        ) from error
    if not address.is_loopback:
        raise ApiSecurityConfigurationError(
            f"AUTOMATA_API_HOST must be a loopback address, got: {host}"
        )


def bearer_token(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    scheme, separator, token = value.strip().partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def token_is_valid(candidate: str | None) -> bool:
    if candidate is None:
        return False
    try:
        expected = get_api_token()
    except ApiSecurityConfigurationError:
        return False
    return secrets.compare_digest(candidate, expected)


def origin_is_allowed(origin: str | None, allowed_origins: Iterable[str]) -> bool:
    if origin is None:
        return True
    return origin in set(allowed_origins)


async def authenticate_websocket(
    websocket: WebSocket,
    *,
    allowed_origins: Iterable[str],
) -> bool:
    origin = websocket.headers.get("origin")
    header_token = bearer_token(websocket.headers.get("authorization"))
    if not origin_is_allowed(origin, allowed_origins):
        await websocket.close(code=4403, reason="Origin not allowed")
        return False

    await websocket.accept()
    if token_is_valid(header_token):
        return True

    try:
        payload = await asyncio.wait_for(
            websocket.receive_json(), timeout=WEBSOCKET_AUTH_TIMEOUT_SECONDS
        )
    except WebSocketDisconnect:
        return False
    except (TimeoutError, ValueError):
        await websocket.close(code=4401, reason="Authentication required")
        return False

    if not isinstance(payload, dict) or payload.get("type") != "authenticate":
        await websocket.close(code=4401, reason="Authentication required")
        return False
    token = payload.get("token")
    if not isinstance(token, str) or not token_is_valid(token):
        await websocket.close(code=4401, reason="Authentication failed")
        return False
    return True
