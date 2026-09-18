"""Process-session helpers for the command tools.

``exec_command`` can hand back a live process session; ``write_stdin``
interacts with one. These helpers bound the transcript the model sees,
render the session snapshot into a tool result, and forward session output
as tool-output events.
"""

from __future__ import annotations

from typing import Any

from automata_api.agent.execution.process_sessions import (
    ProcessSessionError,
    ProcessSessionSnapshot,
    process_session_manager,
)
from automata_api.agent.execution.tool_output import emit_tool_output
from automata_api.agent.tools.args import (
    DEFAULT_STDIN_YIELD_MILLISECONDS,
    json_response,
    max_output_chars_argument,
    string_argument,
    yield_time_ms_argument,
)
from automata_api.agent.tools.models import ToolResult
from automata_api.agent.tools.results import (
    build_exec_output,
    process_session_error_result,
)
from automata_api.agent.tools.text import truncate_head_tail_content


async def run_write_stdin(
    arguments: dict[str, Any],
    workspace: str,
) -> ToolResult:
    """Write to a live process session and return its bounded transcript."""
    del workspace
    session_id = string_argument(arguments, "session_id", "")
    chars_value = arguments.get("chars", "")
    if not session_id:
        return process_session_error_result(
            "write_stdin",
            arguments,
            "missing_session_id",
            "Missing required session_id.",
        )
    if not isinstance(chars_value, str):
        return process_session_error_result(
            "write_stdin",
            arguments,
            "invalid_chars",
            "chars must be a string.",
        )
    yield_time_ms = yield_time_ms_argument(
        arguments,
        default=DEFAULT_STDIN_YIELD_MILLISECONDS,
    )
    assert yield_time_ms is not None
    max_output_chars = max_output_chars_argument(arguments)
    try:
        snapshot = await process_session_manager.interact(
            session_id,
            chars=chars_value,
            yield_time_ms=yield_time_ms,
        )
    except ProcessSessionError as error:
        return process_session_error_result(
            "write_stdin",
            arguments,
            error.code,
            str(error),
        )
    snapshot = bound_process_session_snapshot(snapshot, max_output_chars)
    await emit_process_session_output(snapshot)
    return process_session_tool_result(
        name="write_stdin",
        arguments=arguments,
        snapshot=snapshot,
        max_output_chars=max_output_chars,
        extra={"chars_written": len(chars_value)},
        include_session_id=True,
    )


async def emit_process_session_output(snapshot: ProcessSessionSnapshot) -> None:
    await emit_tool_output("stdout", snapshot.stdout)
    await emit_tool_output("stderr", snapshot.stderr)


def process_session_tool_result(
    *,
    name: str,
    arguments: dict[str, Any],
    snapshot: ProcessSessionSnapshot,
    max_output_chars: int,
    extra: dict[str, Any],
    include_session_id: bool,
) -> ToolResult:
    """Render a session snapshot as the JSON payload the model reads."""
    combined_output, combined_output_truncated = truncate_head_tail_content(
        build_exec_output(snapshot.stdout, snapshot.stderr),
        max_output_chars,
    )
    payload: dict[str, Any] = {
        "simulated": False,
        "ok": snapshot.running
        or (snapshot.exit_code == 0 and not snapshot.timed_out),
        "tool": name,
        "running": snapshot.running,
        "exit_code": snapshot.exit_code,
        "timed_out": snapshot.timed_out,
        "stdout": snapshot.stdout,
        "stderr": snapshot.stderr,
        "output": combined_output,
        "stdout_truncated": snapshot.stdout_truncated,
        "stderr_truncated": snapshot.stderr_truncated,
        "error_code": snapshot.error_code,
        "sandbox": snapshot.sandbox,
        "output_truncated": (
            combined_output_truncated
            or snapshot.stdout_truncated
            or snapshot.stderr_truncated
        ),
        **extra,
    }
    if include_session_id:
        payload["session_id"] = snapshot.session_id
    return ToolResult(
        name=name,
        arguments=arguments,
        content=json_response(payload),
        success=bool(payload["ok"]),
        error_code=snapshot.error_code,
        sandbox=snapshot.sandbox,
    )


def bound_process_session_snapshot(
    snapshot: ProcessSessionSnapshot,
    max_output_chars: int,
) -> ProcessSessionSnapshot:
    """Bound both streams of a snapshot to the same budget."""
    stdout, stdout_truncated = truncate_head_tail_content(
        snapshot.stdout,
        max_output_chars,
    )
    stderr, stderr_truncated = truncate_head_tail_content(
        snapshot.stderr,
        max_output_chars,
    )
    return ProcessSessionSnapshot(
        session_id=snapshot.session_id,
        running=snapshot.running,
        exit_code=snapshot.exit_code,
        timed_out=snapshot.timed_out,
        stdout=stdout,
        stderr=stderr,
        stdout_truncated=snapshot.stdout_truncated or stdout_truncated,
        stderr_truncated=snapshot.stderr_truncated or stderr_truncated,
        error_code=snapshot.error_code,
        sandbox=snapshot.sandbox,
    )
