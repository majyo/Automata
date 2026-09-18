"""The public surface of the tools package.

``automata_api.agent.tools`` re-exports these names so callers and tests
reach the tool layer through one stable entry point. Each name is imported
from the module that owns it; there is no longer a ``_core`` module behind
this, so adding a helper means adding it to its owning module and listing it
here.
"""

from __future__ import annotations

from automata_api.agent.execution.output import (
    CapturedProcessOutput,
    CapturedStream,
    HeadTailTextBuffer,
    append_limited_text,
    capture_process_output,
    read_limited_stream,
)
from automata_api.agent.execution.processes import (
    bash_search_command,
    display_command,
    is_wsl_bash,
    resolve_bash_executable,
    resolve_powershell_executable,
    run_process,
    search_exit_code_is_ok,
)
from automata_api.agent.tools.args import (
    DEFAULT_BASH_TIMEOUT_SECONDS,
    DEFAULT_STDIN_YIELD_MILLISECONDS,
    MAX_BASH_TIMEOUT_SECONDS,
    MAX_PROCESS_YIELD_MILLISECONDS,
    bool_argument,
    json_response,
    max_output_chars_argument,
    parse_tool_arguments,
    positive_int_argument,
    string_argument,
    timeout_argument,
    yield_time_ms_argument,
)
from automata_api.agent.tools.constants import (
    DEFAULT_EXEC_OUTPUT_CHARS,
    FILE_READ_LIMIT,
    MAX_EXEC_OUTPUT_CHARS,
    OUTPUT_LIMIT,
    PROCESS_OUTPUT_CHUNK_BYTES,
    SEARCH_TIMEOUT_SECONDS,
    SUPPORTED_EXEC_SHELLS,
)
from automata_api.agent.tools.models import ToolResult
from automata_api.agent.tools.results import (
    bash_error_result,
    build_exec_output,
    decode_output,
    exec_command_error_result,
    file_error_result,
    patch_error_result,
    process_session_error_result,
    search_error_result,
    search_result_was_no_match,
    search_tool_result,
    truncate_output,
)
from automata_api.agent.tools.text import (
    select_line_range,
    truncate_content,
    truncate_head_tail_content,
)
from automata_api.workspace.patches.parsing import (
    PatchFile,
    PatchHunk,
    PatchHunkLine,
    apply_hunks_to_content,
    diff_header_path,
    parse_patch_file,
    parse_patch_hunk,
    parse_unified_patch,
    patch_file_status,
    patch_summary,
)
from automata_api.workspace.paths import (
    path_argument_for_cwd,
    resolve_executable,
    resolve_file_path,
    resolve_search_path,
    resolve_tool_cwd,
)

__all__ = [
    "CapturedProcessOutput",
    "CapturedStream",
    "DEFAULT_BASH_TIMEOUT_SECONDS",
    "DEFAULT_EXEC_OUTPUT_CHARS",
    "DEFAULT_STDIN_YIELD_MILLISECONDS",
    "FILE_READ_LIMIT",
    "HeadTailTextBuffer",
    "MAX_BASH_TIMEOUT_SECONDS",
    "MAX_EXEC_OUTPUT_CHARS",
    "MAX_PROCESS_YIELD_MILLISECONDS",
    "OUTPUT_LIMIT",
    "PROCESS_OUTPUT_CHUNK_BYTES",
    "PatchFile",
    "PatchHunk",
    "PatchHunkLine",
    "SEARCH_TIMEOUT_SECONDS",
    "SUPPORTED_EXEC_SHELLS",
    "ToolResult",
    "append_limited_text",
    "apply_hunks_to_content",
    "bash_error_result",
    "bash_search_command",
    "bool_argument",
    "build_exec_output",
    "capture_process_output",
    "decode_output",
    "diff_header_path",
    "display_command",
    "exec_command_error_result",
    "file_error_result",
    "is_wsl_bash",
    "json_response",
    "max_output_chars_argument",
    "parse_patch_hunk",
    "parse_patch_file",
    "parse_tool_arguments",
    "parse_unified_patch",
    "patch_error_result",
    "patch_file_status",
    "patch_summary",
    "path_argument_for_cwd",
    "positive_int_argument",
    "process_session_error_result",
    "read_limited_stream",
    "resolve_bash_executable",
    "resolve_executable",
    "resolve_file_path",
    "resolve_powershell_executable",
    "resolve_search_path",
    "resolve_tool_cwd",
    "run_process",
    "search_error_result",
    "search_exit_code_is_ok",
    "search_result_was_no_match",
    "search_tool_result",
    "select_line_range",
    "string_argument",
    "timeout_argument",
    "truncate_content",
    "truncate_head_tail_content",
    "truncate_output",
    "yield_time_ms_argument",
]
