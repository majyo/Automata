from pathlib import Path

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
