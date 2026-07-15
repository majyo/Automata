import asyncio
import ctypes
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from automata_api.agent.execution.approval import (
    ApprovalBroker,
    canonical_arguments_hash,
)
from automata_api.agent.execution.model import CancellationToken, ToolExecutionContext
from automata_api.agent.execution.orchestrator import ToolExecutionOrchestrator
from automata_api.agent.execution.process import (
    process_execution_scope,
    subprocess_group_kwargs,
)
from automata_api.agent.tools._core import ToolResult, capture_process_output
from automata_api.agent.tools.base import AgentTool
from automata_api.agent.tools.model import ToolDescriptor
from automata_api.agent.tools.router import ToolRouter


class RecordingTool(AgentTool):
    def __init__(self, name: str, *, read_only: bool) -> None:
        self.name = name
        self.read_only = read_only
        self.calls: list[dict[str, Any]] = []

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "execution safety test tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        self.calls.append(arguments)
        return ToolResult(self.name, arguments, '{"ok": true}', True)


class ExternalPolicyTool(RecordingTool):
    def __init__(self, action: str) -> None:
        super().__init__("mcp__test__write", read_only=False)
        self.action = action

    def policy_decision(self, _arguments: dict[str, Any], *, mode: str):
        del mode
        return SimpleNamespace(action=self.action, reason=f"mcp_policy_{self.action}")


def descriptor(tool: RecordingTool, risk: str) -> ToolDescriptor:
    return ToolDescriptor(
        name=tool.name,
        spec=tool.spec(),
        executor=tool,
        read_only=tool.read_only,
        risk=risk,
        source="execution-test",
    )


async def execute_with_broker(
    tool: RecordingTool,
    *,
    risk: str,
    mode: str = "act",
    resolve: str | None = None,
):
    emitted: list[dict[str, Any]] = []
    emitted_event = asyncio.Event()

    async def emit(event: dict[str, Any]) -> None:
        emitted.append(event)
        emitted_event.set()

    cancellation = CancellationToken()
    broker = ApprovalBroker(
        run_id="run-1",
        session_id="session-1",
        emit=emit,
        cancellation=cancellation,
    )
    orchestrator = ToolExecutionOrchestrator(approval_broker=broker)
    router = ToolRouter([descriptor(tool, risk)])
    task = asyncio.create_task(
        orchestrator.execute(
            router=router,
            tool_name=tool.name,
            raw_arguments={"value": "test"},
            context=ToolExecutionContext(
                run_id="run-1",
                session_id="session-1",
                tool_call_id="call-1",
                workspace="C:/workspace",
                mode="plan" if mode == "plan" else "act",
                cancellation=cancellation,
            ),
        )
    )
    if resolve is not None:
        await asyncio.wait_for(emitted_event.wait(), timeout=1)
        approval = emitted[-1]
        broker.resolve(
            run_id="run-1",
            approval_id=approval["approval_id"],
            decision=resolve,
        )
    result = await task
    return result, emitted


def test_read_tool_executes_without_approval():
    tool = RecordingTool("read_test", read_only=True)
    result, emitted = asyncio.run(execute_with_broker(tool, risk="read"))

    assert result.success is True
    assert len(tool.calls) == 1
    assert emitted == []


def test_write_tool_requires_approval_and_deny_cannot_be_overridden():
    tool = RecordingTool("write_test", read_only=False)
    result, emitted = asyncio.run(
        execute_with_broker(tool, risk="write", resolve="deny")
    )

    assert result.success is False
    assert tool.calls == []
    assert [event["type"] for event in emitted] == [
        "tool_approval_required",
        "tool_approval_resolved",
    ]
    assert emitted[-1]["decision"] == "deny"


def test_write_tool_executes_after_one_time_approval():
    tool = RecordingTool("write_test", read_only=False)
    result, emitted = asyncio.run(
        execute_with_broker(tool, risk="write", resolve="allow_once")
    )

    assert result.success is True
    assert len(tool.calls) == 1
    assert emitted[-1]["decision"] == "allow_once"


def test_plan_mode_denies_write_without_offering_approval():
    tool = RecordingTool("write_test", read_only=False)
    result, emitted = asyncio.run(
        execute_with_broker(tool, risk="write", mode="plan")
    )

    assert result.success is False
    assert json.loads(result.content)["error"] == "blocked_by_plan_mode"
    assert tool.calls == []
    assert emitted == []


def test_external_prompt_uses_shared_approval_and_external_deny_is_final():
    prompt_tool = ExternalPolicyTool("prompt")
    allowed, emitted = asyncio.run(
        execute_with_broker(prompt_tool, risk="external", resolve="allow_once")
    )
    assert allowed.success is True
    assert emitted[0]["type"] == "tool_approval_required"
    assert emitted[0]["risk"] == "external"

    denied_tool = ExternalPolicyTool("deny")
    denied, emitted = asyncio.run(
        execute_with_broker(denied_tool, risk="external")
    )
    assert denied.success is False
    assert denied_tool.calls == []
    assert emitted == []


def test_approval_argument_hash_is_canonical_and_changes_with_arguments():
    assert canonical_arguments_hash({"b": 2, "a": 1}) == canonical_arguments_hash(
        {"a": 1, "b": 2}
    )
    assert canonical_arguments_hash({"a": 1}) != canonical_arguments_hash({"a": 2})


def test_cancelling_capture_terminates_child_process_tree(tmp_path):
    asyncio.run(assert_cancel_terminates_process_tree(tmp_path))


def test_timeout_terminates_child_process_tree(tmp_path):
    asyncio.run(assert_timeout_terminates_process_tree(tmp_path))


async def assert_cancel_terminates_process_tree(tmp_path: Path) -> None:
    pid_file = tmp_path / "child-pids.txt"
    grandchild_code = "import time; time.sleep(60)"
    child_code = (
        "import os, pathlib, subprocess, sys, time; "
        f"p=subprocess.Popen([sys.executable, '-c', {grandchild_code!r}]); "
        f"pathlib.Path({str(pid_file)!r}).write_text(f'{{os.getpid()}} {{p.pid}}'); "
        "time.sleep(60)"
    )
    parent_code = (
        "import subprocess, sys, time; time.sleep(0.4); "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(60)"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        parent_code,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **subprocess_group_kwargs(),
    )
    with process_execution_scope("tree-run", "tree-call"):
        capture = asyncio.create_task(
            capture_process_output(
                process,
                30,
                stdout_limit=1000,
                stderr_limit=1000,
            )
        )

    for _ in range(100):
        if pid_file.exists():
            break
        await asyncio.sleep(0.05)
    assert pid_file.exists(), "child process did not start"
    child_pid, grandchild_pid = [
        int(value) for value in pid_file.read_text(encoding="utf-8").split()
    ]

    capture.cancel()
    with pytest.raises(asyncio.CancelledError):
        await capture

    assert process.returncode is not None
    for _ in range(60):
        if not process_exists(child_pid) and not process_exists(grandchild_pid):
            break
        await asyncio.sleep(0.05)
    assert not process_exists(child_pid)
    assert not process_exists(grandchild_pid)


async def assert_timeout_terminates_process_tree(tmp_path: Path) -> None:
    pid_file = tmp_path / "timeout-child-pids.txt"
    grandchild_code = "import time; time.sleep(60)"
    child_code = (
        "import os, pathlib, subprocess, sys, time; "
        f"p=subprocess.Popen([sys.executable, '-c', {grandchild_code!r}]); "
        f"pathlib.Path({str(pid_file)!r}).write_text(f'{{os.getpid()}} {{p.pid}}'); "
        "time.sleep(60)"
    )
    parent_code = (
        "import subprocess, sys, time; time.sleep(0.4); "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(60)"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        parent_code,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **subprocess_group_kwargs(),
    )
    with process_execution_scope("timeout-run", "timeout-call"):
        output = await capture_process_output(
            process,
            1.5,
            stdout_limit=1000,
            stderr_limit=1000,
        )

    assert output.timed_out is True
    assert pid_file.exists(), "child process did not start before timeout"
    child_pid, grandchild_pid = [
        int(value) for value in pid_file.read_text(encoding="utf-8").split()
    ]
    for _ in range(60):
        if not process_exists(child_pid) and not process_exists(grandchild_pid):
            break
        await asyncio.sleep(0.05)
    assert not process_exists(child_pid)
    assert not process_exists(grandchild_pid)


def process_exists(pid: int) -> bool:
    if os.name == "nt":
        synchronize = 0x00100000
        wait_timeout = 0x00000102
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
