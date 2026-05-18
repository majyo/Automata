import asyncio
import json

import pytest

from automata_api.services import tools


def test_run_bash_executes_command_in_workspace(tmp_path):
    if tools.resolve_bash_executable() is None:
        pytest.skip("bash is not available")

    result = asyncio.run(
        tools.run_tool(
            "run_bash",
            {"command": "printf hello", "timeout_seconds": 5},
            str(tmp_path),
        )
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["simulated"] is False
    assert payload["ok"] is True
    assert payload["exit_code"] == 0
    assert payload["timed_out"] is False
    assert payload["stdout"] == "hello"
    assert payload["stderr"] == ""
    assert payload["stdout_truncated"] is False
    assert payload["stderr_truncated"] is False


def test_run_bash_rejects_cwd_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = asyncio.run(
        tools.run_tool(
            "run_bash",
            {"command": "pwd", "cwd": ".."},
            str(workspace),
        )
    )
    payload = json.loads(result.content)

    assert result.success is False
    assert payload["simulated"] is False
    assert payload["ok"] is False
    assert "cwd must stay inside workspace" in payload["stderr"]


def test_run_bash_times_out(tmp_path):
    if tools.resolve_bash_executable() is None:
        pytest.skip("bash is not available")

    result = asyncio.run(
        tools.run_tool(
            "run_bash",
            {"command": "sleep 2", "timeout_seconds": 0.1},
            str(tmp_path),
        )
    )
    payload = json.loads(result.content)

    assert result.success is False
    assert payload["simulated"] is False
    assert payload["ok"] is False
    assert payload["exit_code"] is None
    assert payload["timed_out"] is True


def test_run_bash_reports_missing_bash(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "resolve_bash_executable", lambda: None)

    result = asyncio.run(
        tools.run_tool("run_bash", {"command": "printf hello"}, str(tmp_path))
    )
    payload = json.loads(result.content)

    assert result.success is False
    assert payload["simulated"] is False
    assert payload["ok"] is False
    assert "Could not find bash" in payload["stderr"]


def test_rg_search_finds_text(tmp_path):
    source = tmp_path / "sample.txt"
    source.write_text("alpha\nneedle-value\n", encoding="utf-8")

    result = asyncio.run(
        tools.run_tool(
            "rg",
            {"pattern": "needle-value", "path": "sample.txt"},
            str(tmp_path),
        )
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["simulated"] is False
    assert payload["ok"] is True
    assert payload["matched"] is True
    assert payload["tool"] == "rg"
    assert payload["engine"] in {"rg", "grep", "bash"}
    assert "needle-value" in payload["stdout"]


def test_grep_search_finds_text(tmp_path):
    source = tmp_path / "sample.txt"
    source.write_text("alpha\ngrep-value\n", encoding="utf-8")

    result = asyncio.run(
        tools.run_tool(
            "grep",
            {"pattern": "grep-value", "path": "sample.txt"},
            str(tmp_path),
        )
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["simulated"] is False
    assert payload["ok"] is True
    assert payload["matched"] is True
    assert payload["tool"] == "grep"
    assert payload["engine"] in {"grep", "bash"}
    assert "grep-value" in payload["stdout"]


def test_rg_search_no_matches_is_successful(tmp_path):
    source = tmp_path / "sample.txt"
    source.write_text("alpha\n", encoding="utf-8")

    result = asyncio.run(
        tools.run_tool(
            "rg",
            {"pattern": "missing-value", "path": "sample.txt"},
            str(tmp_path),
        )
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["simulated"] is False
    assert payload["ok"] is True
    assert payload["matched"] is False
    assert payload["exit_code"] == 1


def test_rg_search_rejects_path_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = asyncio.run(
        tools.run_tool(
            "rg",
            {"pattern": "anything", "path": ".."},
            str(workspace),
        )
    )
    payload = json.loads(result.content)

    assert result.success is False
    assert payload["simulated"] is False
    assert payload["ok"] is False
    assert "path must stay inside workspace" in payload["stderr"]


def test_rg_search_falls_back_to_bash_when_native_tools_missing(tmp_path, monkeypatch):
    if tools.resolve_bash_executable() is None:
        pytest.skip("bash is not available")

    source = tmp_path / "sample.txt"
    source.write_text("fallback-value\n", encoding="utf-8")
    monkeypatch.setattr(tools, "resolve_executable", lambda _: None)

    result = asyncio.run(
        tools.run_tool(
            "rg",
            {"pattern": "fallback-value", "path": "sample.txt"},
            str(tmp_path),
        )
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["simulated"] is False
    assert payload["ok"] is True
    assert payload["engine"] == "bash"
    assert "fallback-value" in payload["stdout"]
    assert payload["attempts"][0]["engine"] == "rg"
    assert payload["attempts"][1]["engine"] == "grep"
