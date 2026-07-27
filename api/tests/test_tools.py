import asyncio
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from automata_api.agent import tools
from automata_api.agent.execution.process import process_execution_scope
from automata_api.agent.execution.process_sessions import process_session_manager
from automata_api.agent.tools.search import MAX_FILE_LIST_RESULT_CHARS


def patch_text(*lines):
    return "\n".join(lines) + "\n"


def python_shell_command(script: str) -> str:
    return f"{shlex.quote(Path(sys.executable).as_posix())} -c {shlex.quote(script)}"


def test_exec_command_executes_bash_command_in_workspace(tmp_path):
    if tools.resolve_bash_executable() is None:
        pytest.skip("bash is not available")

    result = asyncio.run(
        tools.run_tool(
            "exec_command",
            {"cmd": "printf hello", "timeout_seconds": 5},
            str(tmp_path),
        )
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["simulated"] is False
    assert payload["ok"] is True
    assert payload["tool"] == "exec_command"
    assert payload["cmd"] == "printf hello"
    assert payload["shell"] == "bash"
    assert payload["workdir"] == "."
    assert payload["cwd"] == str(tmp_path.resolve())
    assert payload["shell_path"]
    assert payload["exit_code"] == 0
    assert payload["timed_out"] is False
    assert payload["stdout"] == "hello"
    assert payload["stderr"] == ""
    assert payload["output"] == "hello"
    assert payload["stdout_truncated"] is False
    assert payload["stderr_truncated"] is False
    assert payload["output_truncated"] is False


def test_exec_command_executes_powershell_when_available(tmp_path):
    if tools.resolve_powershell_executable() is None:
        pytest.skip("PowerShell is not available")

    result = asyncio.run(
        tools.run_tool(
            "exec_command",
            {
                "cmd": "Write-Output hello",
                "shell": "powershell",
                "timeout_seconds": 5,
            },
            str(tmp_path),
        )
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["ok"] is True
    assert payload["shell"] == "powershell"
    assert payload["shell_path"]
    assert "hello" in payload["stdout"]
    assert "hello" in payload["output"]


def test_exec_command_rejects_workdir_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = asyncio.run(
        tools.run_tool(
            "exec_command",
            {"cmd": "pwd", "workdir": ".."},
            str(workspace),
        )
    )
    payload = json.loads(result.content)

    assert result.success is False
    assert payload["simulated"] is False
    assert payload["ok"] is False
    assert payload["tool"] == "exec_command"
    assert "cwd must stay inside workspace" in payload["stderr"]
    assert "cwd must stay inside workspace" in payload["output"]


def test_exec_command_rejects_unsupported_shell(tmp_path):
    result = asyncio.run(
        tools.run_tool(
            "exec_command",
            {"cmd": "echo hello", "shell": "cmd"},
            str(tmp_path),
        )
    )
    payload = json.loads(result.content)

    assert result.success is False
    assert payload["simulated"] is False
    assert payload["ok"] is False
    assert payload["shell"] == "cmd"
    assert payload["shell_path"] is None
    assert "Unsupported shell" in payload["stderr"]
    assert payload["supported_shells"] == ["bash", "powershell"]


def test_exec_command_times_out(tmp_path):
    if tools.resolve_bash_executable() is None:
        pytest.skip("bash is not available")

    result = asyncio.run(
        tools.run_tool(
            "exec_command",
            {"cmd": "sleep 2", "timeout_seconds": 0.1},
            str(tmp_path),
        )
    )
    payload = json.loads(result.content)

    assert result.success is False
    assert payload["simulated"] is False
    assert payload["ok"] is False
    assert payload["exit_code"] is None
    assert payload["timed_out"] is True


def test_exec_command_timeout_keeps_captured_prefix(tmp_path):
    if tools.resolve_bash_executable() is None:
        pytest.skip("bash is not available")

    result = asyncio.run(
        tools.run_tool(
            "exec_command",
            {"cmd": "printf before; sleep 3", "timeout_seconds": 1.2},
            str(tmp_path),
        )
    )
    payload = json.loads(result.content)

    assert result.success is False
    assert payload["timed_out"] is True
    assert payload["stdout"] == "before"
    assert payload["stdout_truncated"] is False


def test_exec_command_respects_max_output_chars(tmp_path):
    if tools.resolve_bash_executable() is None:
        pytest.skip("bash is not available")

    result = asyncio.run(
        tools.run_tool(
            "exec_command",
            {"cmd": "printf 1234567890", "max_output_chars": 4},
            str(tmp_path),
        )
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["stdout"] == "1290"
    assert payload["output"] == "1290"
    assert payload["stdout_truncated"] is True
    assert payload["stderr_truncated"] is False
    assert payload["output_truncated"] is True


def test_exec_command_streams_large_stdout_and_stderr_with_limit(tmp_path):
    if tools.resolve_bash_executable() is None:
        pytest.skip("bash is not available")

    result = asyncio.run(
        tools.run_tool(
            "exec_command",
            {
                "cmd": python_shell_command(
                    "import sys; "
                    "sys.stdout.write('o' * 50000); "
                    "sys.stderr.write('e' * 50000)"
                ),
                "max_output_chars": 1024,
                "timeout_seconds": 5,
            },
            str(tmp_path),
        )
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert len(payload["stdout"]) == 1024
    assert payload["stdout"].startswith("o" * 400)
    assert payload["stdout"].endswith("o" * 400)
    assert "... output truncated ..." in payload["stdout"]
    assert len(payload["stderr"]) == 1024
    assert payload["stderr"].startswith("e" * 400)
    assert payload["stderr"].endswith("e" * 400)
    assert "... output truncated ..." in payload["stderr"]
    assert len(payload["output"]) == 1024
    assert payload["output"].startswith("o" * 400)
    assert payload["output"].endswith("o" * 400)
    assert "... output truncated ..." in payload["output"]
    assert payload["stdout_truncated"] is True
    assert payload["stderr_truncated"] is True
    assert payload["output_truncated"] is True


def test_exec_command_live_session_accepts_stdin_and_closes(tmp_path):
    if tools.resolve_bash_executable() is None:
        pytest.skip("bash is not available")

    async def exercise():
        command = python_shell_command(
            "import sys; "
            "print('ready', flush=True); "
            "value = sys.stdin.readline(); "
            "print('received:' + value.strip(), flush=True)"
        )
        with process_execution_scope(
            "run-live",
            "call-exec",
            session_id="conversation-live",
            workspace=str(tmp_path),
        ):
            started = await tools.run_tool(
                "exec_command",
                {
                    "cmd": command,
                    "yield_time_ms": 3000,
                    "timeout_seconds": 10,
                },
                str(tmp_path),
            )
        started_payload = json.loads(started.content)
        assert started.success is True
        assert started_payload["running"] is True
        assert started_payload["stdout"].replace("\r\n", "\n") == "ready\n"
        assert started_payload["session_id"].startswith("proc_")

        with process_execution_scope(
            "run-live",
            "call-stdin",
            session_id="conversation-live",
            workspace=str(tmp_path),
        ):
            completed = await tools.run_tool(
                "write_stdin",
                {
                    "session_id": started_payload["session_id"],
                    "chars": "hello\n",
                    "yield_time_ms": 3000,
                },
                str(tmp_path),
            )
        completed_payload = json.loads(completed.content)
        assert completed.success is True
        assert (
            completed_payload["stdout"].replace("\r\n", "\n")
            == "received:hello\n"
        )
        if completed_payload["running"]:
            with process_execution_scope(
                "run-live",
                "call-final-poll",
                session_id="conversation-live",
                workspace=str(tmp_path),
            ):
                completed = await tools.run_tool(
                    "write_stdin",
                    {
                        "session_id": started_payload["session_id"],
                        "yield_time_ms": 3000,
                    },
                    str(tmp_path),
                )
            completed_payload = json.loads(completed.content)
        assert completed_payload["running"] is False
        assert completed_payload["exit_code"] == 0

        with process_execution_scope(
            "run-live",
            "call-poll-closed",
            session_id="conversation-live",
            workspace=str(tmp_path),
        ):
            closed = await tools.run_tool(
                "write_stdin",
                {"session_id": started_payload["session_id"]},
                str(tmp_path),
            )
        closed_payload = json.loads(closed.content)
        assert closed.success is False
        assert closed_payload["error_code"] == "process_session_not_found"

    asyncio.run(exercise())


def test_write_stdin_rejects_a_different_run_and_cleanup_terminates(tmp_path):
    if tools.resolve_bash_executable() is None:
        pytest.skip("bash is not available")

    async def exercise():
        with process_execution_scope(
            "run-owner",
            "call-exec",
            session_id="conversation-owner",
            workspace=str(tmp_path),
        ):
            started = await tools.run_tool(
                "exec_command",
                {
                    "cmd": python_shell_command(
                        "import time; print('waiting', flush=True); time.sleep(30)"
                    ),
                    "yield_time_ms": 3000,
                    "timeout_seconds": 60,
                },
                str(tmp_path),
            )
        session_id = json.loads(started.content)["session_id"]

        with process_execution_scope(
            "run-other",
            "call-poll",
            session_id="conversation-owner",
            workspace=str(tmp_path),
        ):
            rejected = await tools.run_tool(
                "write_stdin",
                {"session_id": session_id},
                str(tmp_path),
            )
        rejected_payload = json.loads(rejected.content)
        assert rejected.success is False
        assert rejected_payload["error_code"] == "process_session_scope_mismatch"

        await process_session_manager.terminate_run("run-owner")
        with process_execution_scope(
            "run-owner",
            "call-poll-closed",
            session_id="conversation-owner",
            workspace=str(tmp_path),
        ):
            closed = await tools.run_tool(
                "write_stdin",
                {"session_id": session_id},
                str(tmp_path),
            )
        assert json.loads(closed.content)["error_code"] == "process_session_not_found"

    asyncio.run(exercise())


def test_exec_command_live_session_timeout_returns_terminal_result(tmp_path):
    if tools.resolve_bash_executable() is None:
        pytest.skip("bash is not available")

    async def exercise():
        with process_execution_scope(
            "run-timeout",
            "call-exec",
            session_id="conversation-timeout",
            workspace=str(tmp_path),
        ):
            return await tools.run_tool(
                "exec_command",
                {
                    "cmd": python_shell_command("import time; time.sleep(30)"),
                    "yield_time_ms": 3000,
                    "timeout_seconds": 0.1,
                },
                str(tmp_path),
            )

    result = asyncio.run(exercise())
    payload = json.loads(result.content)
    assert result.success is False
    assert payload["running"] is False
    assert payload["timed_out"] is True
    assert payload["exit_code"] is None
    assert "session_id" not in payload


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


def test_rg_files_mode_lists_compact_bounded_paths(tmp_path):
    (tmp_path / "root.py").write_text("", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "child.py").write_text("", encoding="utf-8")
    (nested / "child.txt").write_text("", encoding="utf-8")

    result = asyncio.run(
        tools.run_tool(
            "rg",
            {
                "mode": "files",
                "path": ".",
                "include_globs": ["*.py"],
            },
            str(tmp_path),
        )
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["simulated"] is False
    assert payload["ok"] is True
    assert payload["tool"] == "rg"
    assert payload["mode"] == "files"
    assert payload["files"] == ["nested/child.py", "root.py"]
    assert payload["count"] == 2
    assert payload["truncated"] is False
    assert payload["engine"] in {"rg", "git", "filesystem"}
    assert "stdout" not in payload
    assert "output" not in payload
    assert len(result.content) <= MAX_FILE_LIST_RESULT_CHARS


def test_rg_files_mode_filters_hidden_excluded_and_depth(tmp_path):
    (tmp_path / "root.py").write_text("", encoding="utf-8")
    (tmp_path / ".hidden.py").write_text("", encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "keep.py").write_text("", encoding="utf-8")
    (nested / "skip.py").write_text("", encoding="utf-8")

    default_result = asyncio.run(
        tools.run_tool(
            "rg",
            {
                "mode": "files",
                "exclude_globs": ["nested/skip.py"],
                "max_depth": 1,
            },
            str(tmp_path),
        )
    )
    default_payload = json.loads(default_result.content)
    assert default_payload["files"] == ["root.py"]

    hidden_result = asyncio.run(
        tools.run_tool(
            "rg",
            {
                "mode": "files",
                "hidden": True,
                "include_globs": ["*.py"],
            },
            str(tmp_path),
        )
    )
    hidden_payload = json.loads(hidden_result.content)
    assert hidden_payload["files"] == [
        ".hidden.py",
        "nested/keep.py",
        "nested/skip.py",
        "root.py",
    ]


def test_rg_files_mode_reports_limit_truncation(tmp_path):
    for index in range(4):
        (tmp_path / f"{index}.txt").write_text("", encoding="utf-8")

    result = asyncio.run(
        tools.run_tool(
            "rg",
            {"mode": "files", "limit": 2},
            str(tmp_path),
        )
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["files"] == ["0.txt", "1.txt"]
    assert payload["count"] == 2
    assert payload["truncated"] is True
    assert payload["truncation_reason"] == "file_limit"
    assert "Narrow path" in payload["hint"]


def test_rg_files_mode_reports_character_truncation(tmp_path):
    for index in range(350):
        name = f"{index:03d}-{'x' * 48}.txt"
        (tmp_path / name).write_text("", encoding="utf-8")

    result = asyncio.run(
        tools.run_tool(
            "rg",
            {"mode": "files", "limit": 500},
            str(tmp_path),
        )
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["count"] < 350
    assert payload["truncated"] is True
    assert payload["truncation_reason"] == "character_limit"
    assert len(result.content) <= MAX_FILE_LIST_RESULT_CHARS


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ({"mode": "unknown"}, "invalid_mode"),
        (
            {"mode": "files", "pattern": "needle"},
            "pattern_not_allowed_in_files_mode",
        ),
        ({"mode": "files", "raw_args": ["--pre=write"]}, "unsupported_argument"),
        ({"mode": "files", "include_globs": ["!secret"]}, "invalid_glob"),
        ({"mode": "files", "limit": 0}, "invalid_limit"),
        ({"mode": "files", "max_depth": 65}, "invalid_max_depth"),
    ],
)
def test_rg_files_mode_rejects_invalid_arguments(tmp_path, arguments, error):
    result = asyncio.run(tools.run_tool("rg", arguments, str(tmp_path)))
    payload = json.loads(result.content)

    assert result.success is False
    assert payload["ok"] is False
    assert payload["error"] == error
    assert payload["files"] == []


def test_rg_files_mode_rejects_path_escape_and_file_root(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "sample.txt").write_text("", encoding="utf-8")

    escape_result = asyncio.run(
        tools.run_tool(
            "rg",
            {"mode": "files", "path": ".."},
            str(workspace),
        )
    )
    escape_payload = json.loads(escape_result.content)
    assert escape_result.success is False
    assert escape_payload["error"] == "path_outside_workspace"

    file_result = asyncio.run(
        tools.run_tool(
            "rg",
            {"mode": "files", "path": "sample.txt"},
            str(workspace),
        )
    )
    file_payload = json.loads(file_result.content)
    assert file_result.success is False
    assert file_payload["error"] == "path_not_directory"


def test_rg_files_mode_falls_back_to_git(tmp_path, monkeypatch):
    git = tools.resolve_executable("git")
    if git is None:
        pytest.skip("git is not available")

    subprocess.run(
        [git, "init", "--quiet", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    (tmp_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("", encoding="utf-8")
    original_resolve = tools.resolve_executable
    monkeypatch.setattr(
        "automata_api.agent.tools._core.resolve_executable",
        lambda name: None if name == "rg" else original_resolve(name),
    )

    result = asyncio.run(
        tools.run_tool("rg", {"mode": "files"}, str(tmp_path))
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["engine"] == "git"
    assert payload["ignore_semantics"] == "git"
    assert payload["degraded"] is False
    assert payload["files"] == ["visible.txt"]


def test_rg_files_mode_falls_back_to_filesystem(tmp_path, monkeypatch):
    (tmp_path / "visible.txt").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "automata_api.agent.tools._core.resolve_executable",
        lambda _name: None,
    )

    result = asyncio.run(
        tools.run_tool("rg", {"mode": "files"}, str(tmp_path))
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["engine"] == "filesystem"
    assert payload["ignore_semantics"] == "basic"
    assert payload["degraded"] is True
    assert payload["files"] == ["visible.txt"]


def test_rg_files_mode_filesystem_fallback_skips_symlinks(
    tmp_path, monkeypatch
):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("", encoding="utf-8")
    link = tmp_path / "outside-link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symbolic links are unavailable")
    monkeypatch.setattr(
        "automata_api.agent.tools._core.resolve_executable",
        lambda _name: None,
    )

    result = asyncio.run(
        tools.run_tool("rg", {"mode": "files"}, str(tmp_path))
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["files"] == []


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
        "*** Begin Patch",
        "*** Update File: sample.txt",
        "@@",
        " one",
        "-two",
        "+TWO",
        " three",
        "*** End Patch",
    )

    result = asyncio.run(
        tools.run_tool("apply_patch", {"patch": patch, "dry_run": True}, str(tmp_path))
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["simulated"] is False
    assert payload["ok"] is True
    assert payload["syntax"] == "codex_patch"
    assert payload["dry_run"] is True
    assert payload["summary"] == {
        "added": 0,
        "modified": 1,
        "deleted": 0,
        "moved": 0,
        "hunks": 1,
    }
    assert payload["files"][0]["path"] == "sample.txt"
    assert source.read_text(encoding="utf-8") == "one\ntwo\nthree\n"


def test_apply_patch_apply_modify_changes_expected_content(tmp_path):
    source = tmp_path / "sample.txt"
    source.write_text("one\ntwo\nthree\n", encoding="utf-8")
    patch = patch_text(
        "*** Begin Patch",
        "*** Update File: sample.txt",
        "@@",
        " one",
        "-two",
        "+TWO",
        " three",
        "*** End Patch",
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
        "*** Begin Patch",
        "*** Add File: nested/new.txt",
        "+hello",
        "+world",
        "*** End Patch",
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
        "*** Begin Patch",
        "*** Delete File: delete.txt",
        "*** End Patch",
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
        "*** Begin Patch",
        "*** Update File: a.txt",
        "@@",
        "-before",
        "+after",
        "*** Add File: b.txt",
        "+created",
        "*** End Patch",
    )

    result = asyncio.run(
        tools.run_tool("apply_patch", {"patch": patch, "dry_run": False}, str(tmp_path))
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["summary"] == {
        "added": 1,
        "modified": 1,
        "deleted": 0,
        "moved": 0,
        "hunks": 1,
    }
    assert source.read_text(encoding="utf-8") == "after\n"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "created\n"


def test_apply_patch_context_mismatch_fails_without_writing(tmp_path):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("one\n", encoding="utf-8")
    second.write_text("actual\n", encoding="utf-8")
    patch = patch_text(
        "*** Begin Patch",
        "*** Update File: first.txt",
        "@@",
        "-one",
        "+changed",
        "*** Update File: second.txt",
        "@@",
        "-expected",
        "+changed",
        "*** End Patch",
    )

    result = asyncio.run(
        tools.run_tool("apply_patch", {"patch": patch, "dry_run": False}, str(tmp_path))
    )
    payload = json.loads(result.content)

    assert result.success is False
    assert payload["simulated"] is False
    assert "Failed to find expected lines" in payload["error"]
    assert first.read_text(encoding="utf-8") == "one\n"
    assert second.read_text(encoding="utf-8") == "actual\n"


def test_apply_patch_rejects_path_escape(tmp_path):
    patch = patch_text(
        "*** Begin Patch",
        "*** Add File: ../outside.txt",
        "+nope",
        "*** End Patch",
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
            {
                "patch": patch_text(
                    "*** Begin Patch",
                    "*** Update File: file.txt",
                    "*** End Patch",
                ),
                "dry_run": False,
            },
            str(tmp_path),
        )
    )
    payload = json.loads(result.content)

    assert result.success is False
    assert payload["simulated"] is False
    assert "is empty" in payload["error"]


def test_apply_patch_preview_is_real_dry_run_alias(tmp_path):
    source = tmp_path / "sample.txt"
    source.write_text("one\ntwo\n", encoding="utf-8")
    patch = patch_text(
        "*** Begin Patch",
        "*** Update File: sample.txt",
        "@@",
        " one",
        "-two",
        "+TWO",
        "*** End Patch",
    )

    result = asyncio.run(
        tools.run_tool("apply_patch_preview", {"patch": patch, "dry_run": False}, str(tmp_path))
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["simulated"] is False
    assert payload["tool"] == "apply_patch_preview"
    assert payload["syntax"] == "codex_patch"
    assert payload["dry_run"] is True
    assert source.read_text(encoding="utf-8") == "one\ntwo\n"


def test_apply_patch_rejects_ambiguous_hunk_context(tmp_path):
    source = tmp_path / "sample.txt"
    source.write_text("same\nmiddle\nsame\n", encoding="utf-8")
    patch = patch_text(
        "*** Begin Patch",
        "*** Update File: sample.txt",
        "@@",
        "-same",
        "+changed",
        "*** End Patch",
    )

    result = asyncio.run(
        tools.run_tool("apply_patch", {"patch": patch, "dry_run": False}, str(tmp_path))
    )
    payload = json.loads(result.content)

    assert result.success is False
    assert "not unique" in payload["error"]
    assert source.read_text(encoding="utf-8") == "same\nmiddle\nsame\n"


def test_apply_patch_rejects_insert_only_hunk_without_context(tmp_path):
    source = tmp_path / "sample.txt"
    source.write_text("one\n", encoding="utf-8")
    patch = patch_text(
        "*** Begin Patch",
        "*** Update File: sample.txt",
        "@@",
        "+inserted",
        "*** End Patch",
    )

    result = asyncio.run(
        tools.run_tool("apply_patch", {"patch": patch, "dry_run": False}, str(tmp_path))
    )
    payload = json.loads(result.content)

    assert result.success is False
    assert "must include context or removed lines" in payload["error"]
    assert source.read_text(encoding="utf-8") == "one\n"


def test_apply_patch_accepts_multiple_update_hunks(tmp_path):
    source = tmp_path / "sample.txt"
    source.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    patch = patch_text(
        "*** Begin Patch",
        "*** Update File: sample.txt",
        "@@",
        "-two",
        "+TWO",
        "@@",
        "-four",
        "+FOUR",
        "*** End Patch",
    )

    result = asyncio.run(
        tools.run_tool("apply_patch", {"patch": patch, "dry_run": False}, str(tmp_path))
    )

    assert result.success is True
    assert source.read_text(encoding="utf-8") == "one\nTWO\nthree\nFOUR\n"


def test_apply_patch_moves_file_to_new_path(tmp_path):
    source = tmp_path / "old.txt"
    source.write_text("old\n", encoding="utf-8")
    patch = patch_text(
        "*** Begin Patch",
        "*** Update File: old.txt",
        "*** Move to: nested/new.txt",
        "@@",
        "-old",
        "+new",
        "*** End Patch",
    )

    result = asyncio.run(
        tools.run_tool("apply_patch", {"patch": patch, "dry_run": False}, str(tmp_path))
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["files"][0]["status"] == "moved"
    assert not source.exists()
    assert (tmp_path / "nested" / "new.txt").read_text(encoding="utf-8") == "new\n"


def test_apply_patch_still_accepts_unified_diff_compatibility(tmp_path):
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
        tools.run_tool("apply_patch", {"patch": patch, "dry_run": False}, str(tmp_path))
    )
    payload = json.loads(result.content)

    assert result.success is True
    assert payload["syntax"] == "unified_diff"
    assert source.read_text(encoding="utf-8") == "one\nTWO\n"


def test_run_tool_reports_unknown_tool(tmp_path):
    result = asyncio.run(tools.run_tool("missing_tool", {}, str(tmp_path)))
    payload = json.loads(result.content)

    assert result.success is False
    assert payload["simulated"] is False
    assert payload["ok"] is False
    assert payload["tool"] == "missing_tool"
    assert payload["error"] == "Unknown tool: missing_tool"
