import pytest
from fastapi.testclient import TestClient

from automata_api.main import create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOMATA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AUTOMATA_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("AUTOMATA_CONTEXT_COMPRESSION_ENABLED", raising=False)
    monkeypatch.delenv("AUTOMATA_CONTEXT_MAX_TOKENS", raising=False)
    monkeypatch.delenv("AUTOMATA_CONTEXT_COMPRESSION_TRIGGER_RATIO", raising=False)
    monkeypatch.delenv("AUTOMATA_CONTEXT_COMPRESSION_THRESHOLD_CHARS", raising=False)
    monkeypatch.delenv("AUTOMATA_CONTEXT_COMPRESSION_TARGET_CHARS", raising=False)

    with TestClient(create_app()) as test_client:
        yield test_client
