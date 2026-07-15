import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from automata_api.security import (
    ApiSecurityConfigurationError,
    get_api_token,
    validate_loopback_host,
)


def test_http_api_requires_bearer_token_and_health_stays_minimal(client):
    with TestClient(client.app) as unauthenticated:
        for path in ("/sessions", "/mcp/servers", "/skills"):
            response = unauthenticated.get(path)
            assert response.status_code == 401

        health = unauthenticated.get("/health")
        assert health.status_code == 200
        serialized = health.text.lower()
        assert "deepseek" not in serialized
        assert "token" not in serialized
        assert "base_url" not in serialized


def test_websocket_requires_authentication(client):
    with TestClient(client.app) as unauthenticated:
        with pytest.raises(WebSocketDisconnect) as caught:
            with unauthenticated.websocket_connect("/ws/chat") as websocket:
                websocket.send_json({"type": "prompt"})
                websocket.receive_json()

    assert caught.value.code == 4401


def test_websocket_rejects_untrusted_origin_before_authentication(client):
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect(
            "/ws/chat", headers={"Origin": "https://untrusted.example"}
        ):
            pass

    assert caught.value.code == 4403


def test_api_host_must_be_loopback():
    validate_loopback_host("127.0.0.1")
    validate_loopback_host("::1")
    validate_loopback_host("localhost")

    with pytest.raises(ApiSecurityConfigurationError, match="loopback"):
        validate_loopback_host("0.0.0.0")


def test_missing_api_token_fails_closed(monkeypatch):
    monkeypatch.delenv("AUTOMATA_API_TOKEN", raising=False)

    with pytest.raises(ApiSecurityConfigurationError, match="AUTOMATA_API_TOKEN"):
        get_api_token()
