from pathlib import Path

import pytest

from automata_api import config


def test_load_local_env_searches_frozen_executable_ancestors(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    api_dir = repo_dir / "api"
    sidecar_dir = repo_dir / "ui" / "src-tauri" / "target" / "debug"
    unrelated_dir = tmp_path / "elsewhere"
    fake_executable = sidecar_dir / "automata-api.exe"

    api_dir.mkdir(parents=True)
    sidecar_dir.mkdir(parents=True)
    unrelated_dir.mkdir()
    fake_executable.write_text("", encoding="utf-8")
    (api_dir / ".env").write_text(
        "AUTOMATA_LLM_API_KEY=from-sidecar-tree\n",
        encoding="utf-8",
    )

    monkeypatch.chdir(unrelated_dir)
    monkeypatch.delenv("AUTOMATA_ENV_FILE", raising=False)
    monkeypatch.delenv("AUTOMATA_LLM_API_KEY", raising=False)
    monkeypatch.delenv("AUTOMATA_WORKSPACE_DIR", raising=False)
    monkeypatch.setattr(config, "workspace_dir", lambda: tmp_path / "source")
    monkeypatch.setattr(config, "api_dir", lambda: tmp_path / "source" / "api")
    monkeypatch.setattr(config.sys, "executable", str(fake_executable))
    monkeypatch.setattr(config.sys, "frozen", True, raising=False)

    config.load_local_env()

    assert config.os.environ["AUTOMATA_LLM_API_KEY"] == "from-sidecar-tree"


def test_load_local_env_overrides_empty_process_value(tmp_path, monkeypatch):
    api_dir = tmp_path / "api"
    api_dir.mkdir()
    (api_dir / ".env").write_text(
        "AUTOMATA_LLM_API_KEY=from-env-file\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "")
    monkeypatch.delenv("AUTOMATA_ENV_FILE", raising=False)
    monkeypatch.setattr(config, "workspace_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "api_dir", lambda: api_dir)
    monkeypatch.setattr(config.sys, "frozen", False, raising=False)

    config.load_local_env()

    assert config.os.environ["AUTOMATA_LLM_API_KEY"] == "from-env-file"


def test_env_file_candidates_honors_explicit_env_file_first(tmp_path, monkeypatch):
    env_file = tmp_path / "custom.env"
    monkeypatch.setenv("AUTOMATA_ENV_FILE", str(env_file))
    monkeypatch.setattr(config, "workspace_dir", lambda: tmp_path / "source")
    monkeypatch.setattr(config, "api_dir", lambda: tmp_path / "source" / "api")

    assert config.env_file_candidates()[0] == Path(env_file).resolve()


def test_agent_config_defaults_max_steps(monkeypatch):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    monkeypatch.delenv("AUTOMATA_AGENT_MAX_STEPS", raising=False)

    agent = config.get_agent_config()

    assert agent.max_steps == 24


def test_agent_config_honors_max_steps_env(monkeypatch):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    monkeypatch.setenv("AUTOMATA_AGENT_MAX_STEPS", "40")

    agent = config.get_agent_config()

    assert agent.max_steps == 40


@pytest.mark.parametrize("value", ["0", "-1"])
def test_agent_config_rejects_non_positive_max_steps(monkeypatch, value):
    monkeypatch.setenv("AUTOMATA_LLM_API_KEY", "test-key")
    monkeypatch.setenv("AUTOMATA_AGENT_MAX_STEPS", value)

    with pytest.raises(
        config.AgentConfigurationError,
        match="AUTOMATA_AGENT_MAX_STEPS must be greater than 0.",
    ):
        config.get_agent_config()


def test_context_compression_config_defaults(monkeypatch):
    monkeypatch.delenv("AUTOMATA_CONTEXT_COMPRESSION_ENABLED", raising=False)
    monkeypatch.delenv("AUTOMATA_CONTEXT_MAX_TOKENS", raising=False)
    monkeypatch.delenv("AUTOMATA_CONTEXT_COMPRESSION_TRIGGER_RATIO", raising=False)
    monkeypatch.delenv("AUTOMATA_CONTEXT_COMPRESSION_THRESHOLD_CHARS", raising=False)
    monkeypatch.delenv("AUTOMATA_CONTEXT_COMPRESSION_TARGET_CHARS", raising=False)

    compression = config.get_context_compression_config()

    assert compression.enabled is True
    assert compression.max_context_tokens == 1_000_000
    assert compression.trigger_ratio == 0.8
    assert compression.threshold_chars == 3_200_000
    assert compression.target_chars == 20_000


def test_context_compression_config_honors_env(monkeypatch):
    monkeypatch.setenv("AUTOMATA_CONTEXT_COMPRESSION_ENABLED", "false")
    monkeypatch.setenv("AUTOMATA_CONTEXT_COMPRESSION_THRESHOLD_CHARS", "1234")
    monkeypatch.setenv("AUTOMATA_CONTEXT_COMPRESSION_TARGET_CHARS", "456")

    compression = config.get_context_compression_config()

    assert compression.enabled is False
    assert compression.threshold_chars == 1234
    assert compression.target_chars == 456


def test_context_compression_config_derives_threshold_from_context_limit(monkeypatch):
    monkeypatch.delenv("AUTOMATA_CONTEXT_COMPRESSION_THRESHOLD_CHARS", raising=False)
    monkeypatch.setenv("AUTOMATA_CONTEXT_MAX_TOKENS", "100000")
    monkeypatch.setenv("AUTOMATA_CONTEXT_COMPRESSION_TRIGGER_RATIO", "0.5")

    compression = config.get_context_compression_config()

    assert compression.max_context_tokens == 100_000
    assert compression.trigger_ratio == 0.5
    assert compression.threshold_chars == 200_000


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        (
            "AUTOMATA_CONTEXT_MAX_TOKENS",
            "0",
            "AUTOMATA_CONTEXT_MAX_TOKENS must be greater than 0.",
        ),
        (
            "AUTOMATA_CONTEXT_COMPRESSION_TRIGGER_RATIO",
            "0",
            "AUTOMATA_CONTEXT_COMPRESSION_TRIGGER_RATIO must be greater than 0 "
            "and at most 1.",
        ),
        (
            "AUTOMATA_CONTEXT_COMPRESSION_TRIGGER_RATIO",
            "1.1",
            "AUTOMATA_CONTEXT_COMPRESSION_TRIGGER_RATIO must be greater than 0 "
            "and at most 1.",
        ),
        (
            "AUTOMATA_CONTEXT_COMPRESSION_THRESHOLD_CHARS",
            "0",
            "AUTOMATA_CONTEXT_COMPRESSION_THRESHOLD_CHARS must be greater than 0.",
        ),
        (
            "AUTOMATA_CONTEXT_COMPRESSION_TARGET_CHARS",
            "-1",
            "AUTOMATA_CONTEXT_COMPRESSION_TARGET_CHARS must be greater than 0.",
        ),
        (
            "AUTOMATA_CONTEXT_COMPRESSION_ENABLED",
            "maybe",
            "AUTOMATA_CONTEXT_COMPRESSION_ENABLED must be a boolean.",
        ),
    ],
)
def test_context_compression_config_rejects_invalid_env(
    monkeypatch, name, value, message
):
    monkeypatch.delenv("AUTOMATA_CONTEXT_COMPRESSION_ENABLED", raising=False)
    monkeypatch.delenv("AUTOMATA_CONTEXT_MAX_TOKENS", raising=False)
    monkeypatch.delenv("AUTOMATA_CONTEXT_COMPRESSION_TRIGGER_RATIO", raising=False)
    monkeypatch.delenv("AUTOMATA_CONTEXT_COMPRESSION_THRESHOLD_CHARS", raising=False)
    monkeypatch.delenv("AUTOMATA_CONTEXT_COMPRESSION_TARGET_CHARS", raising=False)
    monkeypatch.setenv(name, value)

    with pytest.raises(config.AgentConfigurationError, match=message):
        config.get_context_compression_config()
