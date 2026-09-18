"""FastAPI dependency entry points.

Transport code obtains the services it needs from ``app.state.container``
through these helpers. The container itself is never passed into business
layers, and no router imports a concrete store or global coordinator.
"""

from __future__ import annotations

from fastapi import Request, WebSocket

from automata_api.bootstrap.container import AppContainer


def container_from_request(request: Request) -> AppContainer:
    """Return the container for an HTTP request's application."""
    return _container(request.app.state)


def container_from_websocket(websocket: WebSocket) -> AppContainer:
    """Return the container for a WebSocket connection's application."""
    return _container(websocket.app.state)


def _container(state: object) -> AppContainer:
    container = getattr(state, "container", None)
    if not isinstance(container, AppContainer):  # pragma: no cover - wiring guard
        raise RuntimeError("Application container is not configured.")
    return container
