"""M1: application container, resource isolation and shutdown ordering.

These tests pin the two properties the container exists to provide:

* two app instances in one process share no mutable application state
* a failure part-way through start-up still releases what was acquired
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from automata_api.bootstrap.container import create_container
from automata_api.bootstrap.lifecycle import app_lifespan
from automata_api.bootstrap.settings import load_settings
from automata_api.main import create_app


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOMATA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "AUTOMATA_API_TOKEN", "test-api-token-that-is-at-least-32-characters"
    )
    monkeypatch.delenv("AUTOMATA_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    return load_settings(load_env=False)


def test_two_containers_do_not_share_resources(settings):
    first = create_container(settings)
    second = create_container(settings)

    assert first is not second
    assert first.event_hub is not second.event_hub
    assert first.process_supervisor is not second.process_supervisor
    assert first.process_sessions is not second.process_sessions
    assert first.coordinator is not second.coordinator
    assert first.coordinator.instance_id != second.coordinator.instance_id


def test_two_apps_expose_distinct_containers(settings):
    first_app = create_app(container=create_container(settings))
    second_app = create_app(container=create_container(settings))

    assert first_app.state.container is not second_app.state.container
    assert (
        first_app.state.container.event_hub
        is not second_app.state.container.event_hub
    )


def test_coordinator_is_built_once_per_container(settings):
    container = create_container(settings)

    assert container.coordinator is container.coordinator
    assert container.coordinator._hub is container.event_hub
    assert container.coordinator._processes is container.process_supervisor
    assert container.coordinator._process_sessions is container.process_sessions


def test_container_receives_the_settings_snapshot(settings):
    container = create_container(settings)

    assert container.settings is settings
    assert (
        container.coordinator._retention_days == settings.run_events.retention_days
    )


def test_container_shares_one_run_store_across_collaborators(settings):
    """The coordinator, the event sink and replay must agree on the store.

    A second store instance would mean two places deciding how run state is
    persisted, so the container builds exactly one.
    """
    container = create_container(settings)

    assert container.coordinator._store is container.run_store
    assert container.replay._store is container.run_store
    assert container.replay is container.replay


def test_container_exposes_a_session_store_by_default(settings):
    container = create_container(settings)

    from automata_api.storage.sqlite.stores import SqliteSessionStore

    assert isinstance(container.session_store, SqliteSessionStore)


def test_connection_uses_the_injected_session_store(settings, monkeypatch):
    """Session validation must go through the injected store, not a global."""
    from automata_api.services.connection import AgentConnection

    container = create_container(settings)
    calls: list[str] = []

    class FakeSessionStore:
        def session_exists(self, session_id: str) -> bool:
            calls.append(session_id)
            return False

    connection = AgentConnection(
        websocket=None,  # type: ignore[arg-type]
        coordinator=container.coordinator,
        event_hub=container.event_hub,
        session_store=FakeSessionStore(),
        replay=container.replay,
    )

    sent: list[dict[str, object]] = []

    class FakeSender:
        async def send_json(self, data):
            sent.append(data)

    connection.sender = FakeSender()  # type: ignore[assignment]
    import asyncio

    asyncio.run(
        connection._handle_payload(
            {"type": "prompt", "session_id": "missing", "prompt": "hello"}
        )
    )

    assert calls == ["missing"]
    assert sent == [{"type": "error", "message": "Session not found"}]


def test_partial_startup_failure_still_releases_observability(
    settings, monkeypatch
):
    """A failure after observability starts must still stop it.

    Observability is started for real and the failure is injected at the
    schema step, so the assertion covers the actual cleanup path: the
    writer task must be gone once start-up has failed.
    """
    container = create_container(settings)

    def failing_init_db():
        raise RuntimeError("schema unavailable")

    monkeypatch.setattr(
        "automata_api.bootstrap.lifecycle.init_db",
        failing_init_db,
    )

    from automata_api.observability import get_observability_manager

    with pytest.raises(RuntimeError, match="schema unavailable"):
        with TestClient(create_app(container=container)):
            pass

    manager = get_observability_manager()
    assert manager.started is False
    assert manager.sampler_task is None
    assert manager.writer is None


def test_shutdown_order_is_stable(settings, monkeypatch):
    """Coordinator settles first, then processes, then subscriptions."""
    container = create_container(settings)
    order: list[str] = []

    def recorder(name: str):
        async def call(*args, **kwargs):
            order.append(name)

        return call

    monkeypatch.setattr(
        container.coordinator, "shutdown", recorder("coordinator.shutdown")
    )
    monkeypatch.setattr(
        container.process_sessions,
        "terminate_all",
        recorder("process_sessions.terminate_all"),
    )
    monkeypatch.setattr(
        container.process_supervisor,
        "terminate_all",
        recorder("process_supervisor.terminate_all"),
    )
    monkeypatch.setattr(
        container.event_hub, "clear", recorder("event_hub.clear")
    )

    import asyncio

    from automata_api.bootstrap.lifecycle import shutdown_container

    asyncio.run(shutdown_container(container))

    assert order == [
        "coordinator.shutdown",
        "process_sessions.terminate_all",
        "process_supervisor.terminate_all",
        "event_hub.clear",
    ]


def test_lifespan_yields_the_container(settings):
    container = create_container(settings)

    import asyncio

    async def run():
        async with app_lifespan(container) as yielded:
            return yielded

    assert asyncio.run(run()) is container
