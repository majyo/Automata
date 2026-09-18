"""FastAPI dependency entry points.

Transport code obtains the services it needs from ``app.state.container``
through these helpers. The container itself is never passed into business
layers, and no router imports a concrete store or global coordinator.
"""

from __future__ import annotations

from fastapi import Request, WebSocket

from automata_api.bootstrap.container import AppContainer
from automata_api.repositories.runs import RunStore
from automata_api.sessions.ports import ConversationStore, SessionStore


def container_from_request(request: Request) -> AppContainer:
    """Return the container for an HTTP request's application."""
    return _container(request.app.state)


def container_from_websocket(websocket: WebSocket) -> AppContainer:
    """Return the container for a WebSocket connection's application."""
    return _container(websocket.app.state)


def run_store(request: Request) -> RunStore:
    """The run store for this request, injected by ``AppContainer``.

    Routers depend on this instead of importing the repository module, so
    the choice of storage implementation stays in the composition root.
    """
    return container_from_request(request).run_store


def session_store(request: Request) -> SessionStore:
    """The session store for this request."""
    return container_from_request(request).session_store


def conversation_store(request: Request) -> ConversationStore:
    """The conversation (messages) store for this request."""
    return container_from_request(request).conversation_store


def _container(state: object) -> AppContainer:
    container = getattr(state, "container", None)
    if not isinstance(container, AppContainer):  # pragma: no cover - wiring guard
        raise RuntimeError("Application container is not configured.")
    return container
