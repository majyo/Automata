import asyncio
import json
import sys
from pathlib import Path

import pytest

from automata_api.agent import tools
from automata_api.agent.tools.base import AgentTool
from automata_api.agent.tools import registry


def patch_text(*lines):
    return "\n".join(lines) + "\n"


def test_tool_specs_include_expected_tool_names():
    specs = tools.tool_specs()
    registered_tools = registry.registered_tools()
    names = {spec["function"]["name"] for spec in specs}

    assert names == {
        "rg",
        "grep",
            "exec_command",
            "write_stdin",
            "run_bash",
        "read_file",
        "write_file",
        "apply_patch",
        "apply_patch_preview",
    }
    assert specs == [tool.spec() for tool in registered_tools]
    assert names == {tool.name for tool in registered_tools}
    assert all(spec["type"] == "function" for spec in specs)

    exec_command_spec = next(
        spec for spec in specs if spec["function"]["name"] == "exec_command"
    )
    exec_command_parameters = exec_command_spec["function"]["parameters"]
    assert exec_command_parameters["required"] == ["cmd"]
    assert exec_command_parameters["properties"]["shell"]["enum"] == [
        "bash",
        "powershell",
    ]


def test_registered_tool_names_are_unique():
    registered_tools = registry.registered_tools()
    names = [tool.name for tool in registered_tools]

    assert all(isinstance(tool, AgentTool) for tool in registered_tools)
    assert len(names) == len(set(names))
    assert registry.build_tool_index(registered_tools) == registry.TOOLS_BY_NAME


def test_registry_rejects_non_agent_tool():
    with pytest.raises(TypeError, match="must extend AgentTool"):
        registry.build_tool_index((object(),))


def test_registry_rejects_empty_tool_name():
    class EmptyNameTool(AgentTool):
        name = ""

        def spec(self):
            return {}

        async def run(self, arguments, workspace):
            raise AssertionError("should not run")

    with pytest.raises(ValueError, match="non-empty string"):
        registry.build_tool_index((EmptyNameTool(),))


def test_registry_rejects_duplicate_tool_names():
    duplicate_tools = (registry.registered_tools()[0], registry.registered_tools()[0])

    with pytest.raises(ValueError, match="Duplicate tool registered"):
        registry.build_tool_index(duplicate_tools)


def test_parse_tool_arguments_accepts_empty_dict_and_json_object():
    assert tools.parse_tool_arguments(None) == ({}, None)
    assert tools.parse_tool_arguments("") == ({}, None)
    assert tools.parse_tool_arguments({"path": "x"}) == ({"path": "x"}, None)
    assert tools.parse_tool_arguments('{"path": "x"}') == ({"path": "x"}, None)


def test_parse_tool_arguments_rejects_invalid_json_and_non_object():
    parsed, error = tools.parse_tool_arguments("{bad")
    assert parsed == {}
    assert "Invalid JSON arguments" in error

    parsed, error = tools.parse_tool_arguments('["not", "object"]')
    assert parsed == {}
    assert error == "Tool arguments must be a JSON object."


def test_resolve_file_path_accepts_relative_and_rejects_escape(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    resolved = tools.resolve_file_path(workspace.resolve(), "nested/file.txt")
    missing = tools.resolve_file_path(workspace.resolve(), "")
    escaped = tools.resolve_file_path(workspace.resolve(), "../outside.txt")

    assert resolved == (workspace / "nested/file.txt").resolve()
    assert missing == "Missing required path."
    assert "path must stay inside workspace" in escaped


def test_resolve_search_path_requires_existing_path_inside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "source.txt"
    source.write_text("hello", encoding="utf-8")

    resolved = tools.resolve_search_path(
        workspace_path=workspace.resolve(),
        cwd_path=workspace.resolve(),
        raw_path="source.txt",
    )
    missing = tools.resolve_search_path(
        workspace_path=workspace.resolve(),
        cwd_path=workspace.resolve(),
        raw_path="missing.txt",
    )
    escaped = tools.resolve_search_path(
        workspace_path=workspace.resolve(),
        cwd_path=workspace.resolve(),
        raw_path="../outside.txt",
    )

    assert resolved == source.resolve()
    assert "path does not exist" in missing
    assert "path must stay inside workspace" in escaped


def test_line_bool_positive_and_truncation_helpers():
    selected, start, end, total = tools.select_line_range("a\nb\nc\n", "2", 3)

    assert selected == "b\nc\n"
    assert start == 2
    assert end == 3
    assert total == 3
    assert tools.select_line_range("a\nb\n", 3, 2) == ("", 3, 2, 2)
    assert tools.positive_int_argument(True) is None
    assert tools.positive_int_argument("4") == 4
    assert tools.positive_int_argument("bad") is None
    assert tools.bool_argument({"flag": True}, "flag", False) is True
    assert tools.bool_argument({"flag": "true"}, "flag", False) is False
    assert tools.truncate_content("abcdef", 3) == ("abc", True)
    assert tools.truncate_content("abc", 3) == ("abc", False)


def test_read_limited_stream_handles_split_utf8_sequence():
    async def run():
        reader = asyncio.StreamReader()
        encoded = "éabc".encode("utf-8")
        reader.feed_data(encoded[:1])
        reader.feed_data(encoded[1:])
        reader.feed_eof()
        return await tools.read_limited_stream(reader, 2, chunk_size=1)

    result = asyncio.run(run())

    assert result.text == "éc"
    assert result.truncated is True
    assert result.bytes_seen == len("éabc".encode("utf-8"))


def test_capture_process_output_limits_stdout_and_stderr():
    async def run():
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            (
                "import sys; "
                "sys.stdout.write('o' * 50000); "
                "sys.stderr.write('e' * 50000)"
            ),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return await tools.capture_process_output(
            process,
            timeout_seconds=5,
            stdout_limit=17,
            stderr_limit=19,
        )

    result = asyncio.run(run())

    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.stdout.text == "o" * 17
    assert result.stderr.text == "e" * 19
    assert result.stdout.truncated is True
    assert result.stderr.truncated is True
    assert result.stdout.bytes_seen == 50000
    assert result.stderr.bytes_seen == 50000


def test_unified_patch_parser_parses_add_modify_delete_and_rejects_binary():
    parsed, error = tools.parse_unified_patch(
        patch_text(
            "diff --git a/a.txt b/a.txt",
            "--- a/a.txt",
            "+++ b/a.txt",
            "@@ -1 +1 @@",
            "-old",
            "+new",
            "--- /dev/null",
            "+++ b/new.txt",
            "@@ -0,0 +1 @@",
            "+created",
            "--- a/delete.txt",
            "+++ /dev/null",
            "@@ -1 +0,0 @@",
            "-gone",
        )
    )
    binary, binary_error = tools.parse_unified_patch("GIT binary patch\n")

    assert error is None
    assert [file.old_path for file in parsed] == ["a.txt", None, "delete.txt"]
    assert [file.new_path for file in parsed] == ["a.txt", "new.txt", None]
    assert [tools.patch_file_status(file) for file in parsed] == [
        "modified",
        "added",
        "deleted",
    ]
    assert binary == []
    assert binary_error == "Binary patches are not supported."


def test_diff_header_path_validates_relative_workspace_paths():
    assert tools.diff_header_path("--- a/src/app.py", "--- ") == ("src/app.py", None)
    assert tools.diff_header_path("+++ /dev/null", "+++ ") == (None, None)

    absolute_path, absolute_error = tools.diff_header_path("+++ C:/tmp/file.py", "+++ ")
    escaped_path, escaped_error = tools.diff_header_path("+++ b/../file.py", "+++ ")

    assert absolute_path is None
    assert "must be relative" in absolute_error
    assert escaped_path is None
    assert "must not escape the workspace" in escaped_error


def test_apply_hunks_to_content_success_and_context_mismatch():
    parsed, error = tools.parse_unified_patch(
        patch_text(
            "--- a/sample.txt",
            "+++ b/sample.txt",
            "@@ -1,3 +1,3 @@",
            " one",
            "-two",
            "+TWO",
            " three",
        )
    )
    assert error is None

    new_content, apply_error = tools.apply_hunks_to_content(
        "one\ntwo\nthree\n",
        parsed[0].hunks,
        "sample.txt",
    )
    mismatch_content, mismatch_error = tools.apply_hunks_to_content(
        "one\nwrong\nthree\n",
        parsed[0].hunks,
        "sample.txt",
    )

    assert new_content == "one\nTWO\nthree\n"
    assert apply_error is None
    assert mismatch_content == ""
    assert "Hunk context mismatch" in mismatch_error


def test_patch_summary_and_error_result():
    summary = tools.patch_summary(
        [
            {"status": "added", "hunks": 1},
            {"status": "modified", "hunks": 2},
            {"status": "deleted", "hunks": 1},
            {"status": "moved", "hunks": 1},
        ]
    )
    error = tools.patch_error_result(
        tool_name="apply_patch",
        arguments={"patch": ""},
        dry_run=True,
        error="bad patch",
        path="sample.txt",
    )

    assert summary == {"added": 1, "modified": 1, "deleted": 1, "moved": 1, "hunks": 5}
    payload = json.loads(error.content)
    assert error.success is False
    assert payload["path"] == "sample.txt"
    assert payload["error"] == "bad patch"


def test_search_and_command_helpers():
    no_match = tools.ToolResult(
        name="rg",
        arguments={},
        content=json.dumps({"ok": True, "matched": False}),
        success=True,
    )
    invalid_content = tools.ToolResult("rg", {}, "not json", False)

    assert tools.path_argument_for_cwd(Path("a/b").resolve(), Path("a").resolve()).endswith("b")
    assert "rg --line-number" in tools.bash_search_command("rg", "needle", ".")
    assert "grep -R -n" in tools.bash_search_command("grep", "needle", ".")
    assert tools.display_command(["rg", "two words"]) == "rg 'two words'"
    assert tools.search_exit_code_is_ok(0) is True
    assert tools.search_exit_code_is_ok(1) is True
    assert tools.search_exit_code_is_ok(2) is False
    assert tools.search_result_was_no_match(no_match) is True
    assert tools.search_result_was_no_match(invalid_content) is False


def test_timeout_and_output_helpers():
    assert tools.timeout_argument({}) == tools.DEFAULT_BASH_TIMEOUT_SECONDS
    assert tools.timeout_argument({"timeout_seconds": "5"}) == 5.0
    assert tools.timeout_argument({"timeout_seconds": -1}) == tools.DEFAULT_BASH_TIMEOUT_SECONDS
    assert tools.timeout_argument({"timeout_seconds": 999}) == tools.MAX_BASH_TIMEOUT_SECONDS
    assert tools.decode_output(b"hello") == "hello"
    assert tools.truncate_output("x" * (tools.OUTPUT_LIMIT + 1))[1] is True
    assert tools.string_argument({"name": " value "}, "name", "default") == " value "
    assert tools.string_argument({"name": ""}, "name", "default") == "default"
