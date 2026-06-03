import asyncio
import json

import pytest

from automata_api.agent import tools


def patch_text(*lines):
    return "\n".join(lines) + "\n"


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
    monkeypatch.setattr(
        "automata_api.agent.tools._core.resolve_bash_executable", lambda: None
    )

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
    monkeypatch.setattr("automata_api.agent.tools._core.resolve_executable", lambda _: None)

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


def test_apply_patch_dry_run_modify_leaves_file_unchanged(tmp_path):
    source = tmp_path / "sample.txt"
    source.write_text("one\ntwo\nthree\n", encoding="utf-8")
    patch = patch_text(
        "--- a/sample.txt",
        "+++ b/sample.txt",
        "@@ -1,3 +1,3 @@",
        " one",
        "-two",
        "+TWO",
        " three",
    )

    result = asyncio.run(
        tools.run_tool("apply_patch", {"patch": patch, "dry_run": True}, str(tmp_path))
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["simulated"] is False
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["summary"] == {"added": 0, "modified": 1, "deleted": 0, "hunks": 1}
    assert payload["files"][0]["path"] == "sample.txt"
    assert source.read_text(encoding="utf-8") == "one\ntwo\nthree\n"


def test_apply_patch_apply_modify_changes_expected_content(tmp_path):
    source = tmp_path / "sample.txt"
    source.write_text("one\ntwo\nthree\n", encoding="utf-8")
    patch = patch_text(
        "--- a/sample.txt",
        "+++ b/sample.txt",
        "@@ -1,3 +1,3 @@",
        " one",
        "-two",
        "+TWO",
        " three",
    )

    result = asyncio.run(
        tools.run_tool("apply_patch", {"patch": patch, "dry_run": False}, str(tmp_path))
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["dry_run"] is False
    assert payload["files"][0]["status"] == "modified"
    assert source.read_text(encoding="utf-8") == "one\nTWO\nthree\n"


def test_apply_patch_add_file_creates_text_file(tmp_path):
    patch = patch_text(
        "--- /dev/null",
        "+++ b/nested/new.txt",
        "@@ -0,0 +1,2 @@",
        "+hello",
        "+world",
    )

    result = asyncio.run(
        tools.run_tool("apply_patch", {"patch": patch, "dry_run": False}, str(tmp_path))
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["files"][0]["status"] == "added"
    assert (tmp_path / "nested" / "new.txt").read_text(encoding="utf-8") == "hello\nworld\n"


def test_apply_patch_delete_file_removes_target(tmp_path):
    source = tmp_path / "delete.txt"
    source.write_text("alpha\nbeta\n", encoding="utf-8")
    patch = patch_text(
        "--- a/delete.txt",
        "+++ /dev/null",
        "@@ -1,2 +0,0 @@",
        "-alpha",
        "-beta",
    )

    result = asyncio.run(
        tools.run_tool("apply_patch", {"patch": patch, "dry_run": False}, str(tmp_path))
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["files"][0]["status"] == "deleted"
    assert not source.exists()


def test_apply_patch_multiple_files_apply_atomically(tmp_path):
    source = tmp_path / "a.txt"
    source.write_text("before\n", encoding="utf-8")
    patch = patch_text(
        "--- a/a.txt",
        "+++ b/a.txt",
        "@@ -1 +1 @@",
        "-before",
        "+after",
        "--- /dev/null",
        "+++ b/b.txt",
        "@@ -0,0 +1 @@",
        "+created",
    )

    result = asyncio.run(
        tools.run_tool("apply_patch", {"patch": patch, "dry_run": False}, str(tmp_path))
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["summary"] == {"added": 1, "modified": 1, "deleted": 0, "hunks": 2}
    assert source.read_text(encoding="utf-8") == "after\n"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "created\n"


def test_apply_patch_context_mismatch_fails_without_writing(tmp_path):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("one\n", encoding="utf-8")
    second.write_text("actual\n", encoding="utf-8")
    patch = patch_text(
        "--- a/first.txt",
        "+++ b/first.txt",
        "@@ -1 +1 @@",
        "-one",
        "+changed",
        "--- a/second.txt",
        "+++ b/second.txt",
        "@@ -1 +1 @@",
        "-expected",
        "+changed",
    )

    result = asyncio.run(
        tools.run_tool("apply_patch", {"patch": patch, "dry_run": False}, str(tmp_path))
    )
    payload = json.loads(result.content)

    assert result.success is False
    assert payload["simulated"] is False
    assert "Hunk context mismatch" in payload["error"]
    assert first.read_text(encoding="utf-8") == "one\n"
    assert second.read_text(encoding="utf-8") == "actual\n"


def test_apply_patch_rejects_path_escape(tmp_path):
    patch = patch_text(
        "--- /dev/null",
        "+++ b/../outside.txt",
        "@@ -0,0 +1 @@",
        "+nope",
    )

    result = asyncio.run(
        tools.run_tool("apply_patch", {"patch": patch, "dry_run": False}, str(tmp_path))
    )
    payload = json.loads(result.content)

    assert result.success is False
    assert payload["simulated"] is False
    assert "must not escape the workspace" in payload["error"]
    assert not (tmp_path.parent / "outside.txt").exists()


def test_apply_patch_rejects_malformed_patch(tmp_path):
    result = asyncio.run(
        tools.run_tool(
            "apply_patch",
            {"patch": patch_text("--- a/file.txt", "+++ b/file.txt"), "dry_run": False},
            str(tmp_path),
        )
    )
    payload = json.loads(result.content)

    assert result.success is False
    assert payload["simulated"] is False
    assert "has no content hunks" in payload["error"]


def test_apply_patch_preview_is_real_dry_run_alias(tmp_path):
    source = tmp_path / "sample.txt"
    source.write_text("one\ntwo\n", encoding="utf-8")
    patch = patch_text(
        "--- a/sample.txt",
        "+++ b/sample.txt",
        "@@ -1,2 +1,2 @@",
        " one",
        "-two",
        "+TWO",
    )

    result = asyncio.run(
        tools.run_tool("apply_patch_preview", {"patch": patch, "dry_run": False}, str(tmp_path))
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["simulated"] is False
    assert payload["tool"] == "apply_patch_preview"
    assert payload["dry_run"] is True
    assert source.read_text(encoding="utf-8") == "one\ntwo\n"


def test_run_tool_reports_unknown_tool(tmp_path):
    result = asyncio.run(tools.run_tool("missing_tool", {}, str(tmp_path)))
    payload = json.loads(result.content)

    assert result.success is False
    assert payload["simulated"] is False
    assert payload["ok"] is False
    assert payload["tool"] == "missing_tool"
    assert payload["error"] == "Unknown tool: missing_tool"
