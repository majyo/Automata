from automata_api.agent import prompts, status
from automata_api.config import AgentConfig, AgentConfigurationError


def test_agent_workspace_prefers_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOMATA_WORKSPACE_DIR", str(tmp_path))

    assert prompts.agent_workspace() == str(tmp_path)


def test_agent_workspace_falls_back_to_configured_workspace(monkeypatch, tmp_path):
    monkeypatch.delenv("AUTOMATA_WORKSPACE_DIR", raising=False)
    monkeypatch.setattr(prompts, "workspace_dir", lambda: tmp_path)

    assert prompts.agent_workspace() == str(tmp_path)


def test_agent_system_prompt_includes_workspace_and_tool_guidance(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOMATA_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(prompts, "get_system_prompt", lambda: "Base system prompt")

    prompt = prompts.agent_system_prompt()

    assert "Base system prompt" in prompt
    assert f"Current workspace: {tmp_path}" in prompt
    assert "exec_command" in prompt
    assert "shell=bash" in prompt
    assert "shell=powershell" in prompt
    assert "run_bash" in prompt
    assert "write_file" in prompt
    assert "apply_patch" in prompt
    assert "Codex-style patches" in prompt
    assert "*** Begin Patch" in prompt


def test_plan_system_prompt_includes_allowed_and_blocked_tools(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOMATA_WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(prompts, "get_system_prompt", lambda: "Base system prompt")

    prompt = prompts.plan_system_prompt()

    assert "Base system prompt" in prompt
    assert f"Current workspace: {tmp_path}" in prompt
    assert "backend Plan mode" in prompt
    assert "Allowed tools in Plan mode are read_file, rg, grep" in prompt
    assert "Do not call exec_command, run_bash, write_file" in prompt
    assert "Codex-style patches" in prompt


def test_approved_plan_message_wraps_content():
    message = prompts.approved_plan_message("1. Change the code")

    assert message["role"] == "system"
    assert "approved the following implementation plan" in message["content"]
    assert "1. Change the code" in message["content"]


def test_agent_status_reports_ready(monkeypatch):
    monkeypatch.setattr(
        status,
        "get_agent_config",
        lambda: AgentConfig(
            api_key="test-key",
            base_url="https://provider.test",
            model="unit-model",
            timeout_seconds=30.0,
            temperature=0.2,
        ),
    )

    assert status.agent_status() == {
        "status": "ready",
        "message": "DeepSeek agent configured",
        "base_url": "https://provider.test",
        "model": "unit-model",
    }
    assert status.agent_ready_message() == "DeepSeek agent ready (unit-model)"


def test_agent_status_reports_missing_config_with_env_defaults(monkeypatch):
    monkeypatch.setenv("AUTOMATA_LLM_BASE_URL", "https://env-provider.test")
    monkeypatch.setenv("AUTOMATA_LLM_MODEL", "env-model")

    def raise_config_error():
        raise AgentConfigurationError("missing key")

    monkeypatch.setattr(status, "get_agent_config", raise_config_error)

    assert status.agent_status() == {
        "status": "missing_config",
        "message": "missing key",
        "base_url": "https://env-provider.test",
        "model": "env-model",
    }
    assert status.agent_ready_message() == "missing key"
