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


def test_read_file_reads_real_workspace_file(tmp_path):
    source = tmp_path / "sample.txt"
    source.write_text("one\ntwo\nthree\n", encoding="utf-8")

    result = asyncio.run(
        tools.run_tool("read_file", {"path": "sample.txt"}, str(tmp_path))
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["simulated"] is False
    assert payload["ok"] is True
    assert payload["path"] == "sample.txt"
    assert payload["content"] == "one\ntwo\nthree\n"
    assert payload["truncated"] is False


def test_read_file_supports_line_range(tmp_path):
    source = tmp_path / "sample.txt"
    source.write_text("one\ntwo\nthree\n", encoding="utf-8")

    result = asyncio.run(
        tools.run_tool(
            "read_file",
            {"path": "sample.txt", "start_line": 2, "end_line": 3},
            str(tmp_path),
        )
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["content"] == "two\nthree\n"
    assert payload["start_line"] == 2
    assert payload["end_line"] == 3
    assert payload["total_lines"] == 3


def test_read_file_rejects_path_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = asyncio.run(
        tools.run_tool("read_file", {"path": ".."}, str(workspace))
    )
    payload = json.loads(result.content)

    assert result.success is False
    assert payload["simulated"] is False
    assert payload["ok"] is False
    assert "path must stay inside workspace" in payload["error"]


def test_write_file_creates_appends_and_overwrites(tmp_path):
    result = asyncio.run(
        tools.run_tool(
            "write_file",
            {"path": "nested/sample.txt", "content": "one\n", "mode": "create"},
            str(tmp_path),
        )
    )
    payload = json.loads(result.content)
    assert result.success is True
    assert payload["simulated"] is False
    assert payload["ok"] is True
    assert payload["mode"] == "create"

    append_result = asyncio.run(
        tools.run_tool(
            "write_file",
            {"path": "nested/sample.txt", "content": "two\n", "mode": "append"},
            str(tmp_path),
        )
    )
    assert append_result.success is True
    assert (tmp_path / "nested" / "sample.txt").read_text(encoding="utf-8") == "one\ntwo\n"

    overwrite_result = asyncio.run(
        tools.run_tool(
            "write_file",
            {"path": "nested/sample.txt", "content": "final\n"},
            str(tmp_path),
        )
    )
    overwrite_payload = json.loads(overwrite_result.content)
    assert overwrite_result.success is True
    assert overwrite_payload["mode"] == "overwrite"
    assert (tmp_path / "nested" / "sample.txt").read_text(encoding="utf-8") == "final\n"


def test_write_file_create_mode_rejects_existing_file(tmp_path):
    source = tmp_path / "sample.txt"
    source.write_text("existing", encoding="utf-8")

    result = asyncio.run(
        tools.run_tool(
            "write_file",
            {"path": "sample.txt", "content": "new", "mode": "create"},
            str(tmp_path),
        )
    )
    payload = json.loads(result.content)

    assert result.success is False
    assert payload["simulated"] is False
    assert payload["ok"] is False
    assert "File already exists" in payload["error"]
    assert source.read_text(encoding="utf-8") == "existing"


def test_write_file_rejects_path_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = asyncio.run(
        tools.run_tool(
            "write_file",
            {"path": "../outside.txt", "content": "nope"},
            str(workspace),
        )
    )
    payload = json.loads(result.content)

    assert result.success is False
    assert payload["simulated"] is False
    assert payload["ok"] is False
    assert "path must stay inside workspace" in payload["error"]
