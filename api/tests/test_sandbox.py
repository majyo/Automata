import asyncio
import json
import os
from types import SimpleNamespace

import pytest

from automata_api.agent.execution.approval import ApprovalBroker
from automata_api.agent.execution.model import (
    CancellationToken,
    ToolExecutionContext,
)
from automata_api.agent.execution.orchestrator import ToolExecutionOrchestrator
from automata_api.agent.execution.permissions import (
    compile_permission_profile,
    permission_profile_from_json,
    sandbox_backend_for_profile,
)
from automata_api.agent.execution.process import current_process_scope
from automata_api.agent.execution.sandbox import process_launcher
from automata_api.agent.execution.sandbox.backends.linux import (
    LinuxSandboxBackend,
)
from automata_api.agent.execution.sandbox.backends.macos import (
    _seatbelt_profile,
)
from automata_api.agent.execution.sandbox.backends.windows import (
    find_windows_sandbox_host,
)
from automata_api.agent.execution.sandbox.environment import (
    build_tool_environment,
)
from automata_api.agent.execution.sandbox.model import (
    ProcessLaunchRequest,
    SandboxMetadata,
)
from automata_api.agent.execution.sandbox.protocol import (
    classify_sandbox_failure,
)
from automata_api.agent.tools._core import ToolResult
from automata_api.agent.tools.base import AgentTool
from automata_api.agent.tools.model import ToolDescriptor
from automata_api.agent.tools.router import ToolRouter


class RetryTool(AgentTool):
    name = "retry_test"
    read_only = True

    def __init__(self) -> None:
        self.enforcements: list[str] = []

    def spec(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": "sandbox retry test",
                "parameters": {"type": "object", "properties": {}},
            },
        }

    async def run(self, arguments):
        scope = current_process_scope()
        assert scope is not None
        assert scope.permission_profile is not None
        enforcement = scope.permission_profile.sandbox_enforcement
        self.enforcements.append(enforcement)
        if enforcement == "managed":
            return ToolResult(
                self.name,
                arguments,
                '{"ok": false, "error_code": "sandbox_denied"}',
                False,
                error_code="sandbox_denied",
            )
        return ToolResult(self.name, arguments, '{"ok": true}', True)


def retry_router(tool: RetryTool) -> ToolRouter:
    return ToolRouter(
        [
            ToolDescriptor(
                name=tool.name,
                spec=tool.spec(),
                executor=tool,
                read_only=True,
                risk="read",
                source="sandbox-test",
            )
        ]
    )


def test_permission_profiles_are_hashed_immutable_and_platform_routed(tmp_path):
    managed = compile_permission_profile(
        "default",
        workspace=tmp_path,
        temporary_paths=(tmp_path,),
        runtime_paths=(),
    )
    direct = compile_permission_profile(
        "full_access",
        workspace=tmp_path,
        temporary_paths=(tmp_path,),
        runtime_paths=(),
    )

    assert managed.sandbox_enforcement == "managed"
    assert managed.network == "restricted"
    assert direct.sandbox_enforcement == "disabled"
    assert direct.file_system.kind == "unrestricted"
    assert permission_profile_from_json(managed.to_json()) == managed
    assert sandbox_backend_for_profile(managed, platform="win32") == (
        "windows-appcontainer"
    )
    assert sandbox_backend_for_profile(managed, platform="linux") == "linux-bwrap"
    assert sandbox_backend_for_profile(managed, platform="darwin") == (
        "macos-seatbelt"
    )

    payload = json.loads(managed.to_json())
    payload["network"] = "enabled"
    with pytest.raises(ValueError):
        permission_profile_from_json(json.dumps(payload))
    payload = json.loads(managed.to_json())
    payload["version"] = 999
    with pytest.raises(ValueError, match="Unsupported"):
        permission_profile_from_json(json.dumps(payload))


def test_environment_policy_strips_ambient_secrets_but_keeps_explicit_values(
    tmp_path,
):
    profile = compile_permission_profile(
        "default",
        workspace=tmp_path,
        temporary_paths=(tmp_path,),
        runtime_paths=(),
    )
    environment = build_tool_environment(
        profile,
        {
            "PATH": "safe-path",
            "AUTOMATA_API_TOKEN": "ambient-secret",
            "MCP_CUSTOM_KEY": "explicit-value",
        },
        explicit_names=("MCP_CUSTOM_KEY",),
    )

    assert environment["PATH"] == "safe-path"
    assert environment["MCP_CUSTOM_KEY"] == "explicit-value"
    assert "AUTOMATA_API_TOKEN" not in environment


def test_sandbox_protocol_distinguishes_denial_from_ordinary_nonzero_exit():
    metadata = SandboxMetadata(
        enforcement="managed",
        backend="test",
        profile_hash="hash",
    )
    assert (
        classify_sandbox_failure(
            exit_code=2,
            stderr="ordinary command failure",
            metadata=metadata,
        )
        is None
    )
    failure = classify_sandbox_failure(
        exit_code=1,
        stderr="Access is denied.\r\n",
        metadata=metadata,
    )
    assert failure is not None
    assert failure.code == "sandbox_denied"


def test_linux_bwrap_policy_uses_namespaces_read_only_root_and_write_bind(
    tmp_path,
    monkeypatch,
):
    profile = compile_permission_profile(
        "default",
        workspace=tmp_path,
        temporary_paths=(tmp_path,),
        runtime_paths=(),
    )
    captured = {}

    async def fake_spawn(*argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return SimpleNamespace(pid=1)

    monkeypatch.setattr(
        "automata_api.agent.execution.sandbox.backends.linux.shutil.which",
        lambda name: "/usr/bin/bwrap" if name == "bwrap" else None,
    )
    monkeypatch.setattr(
        "automata_api.agent.execution.sandbox.backends.linux.asyncio.create_subprocess_exec",
        fake_spawn,
    )
    request = ProcessLaunchRequest(
        argv=("sh", "-c", "true"),
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        stdin=None,
        stdout=None,
        stderr=None,
        profile=profile,
        scope_name="test",
    )

    asyncio.run(LinuxSandboxBackend().spawn(request))

    argv = captured["argv"]
    assert "--ro-bind" in argv
    assert "--unshare-net" in argv
    assert "--unshare-pid" in argv
    assert "--cap-drop" in argv
    assert ("--bind", str(tmp_path), str(tmp_path)) == tuple(
        argv[argv.index("--bind") : argv.index("--bind") + 3]
    )


def test_macos_seatbelt_policy_denies_network_and_protects_metadata(tmp_path):
    protected = tmp_path / ".git"
    protected.mkdir()
    profile = compile_permission_profile(
        "default",
        workspace=tmp_path,
        temporary_paths=(tmp_path,),
        runtime_paths=(),
    )
    request = ProcessLaunchRequest(
        argv=("sh", "-c", "true"),
        cwd=tmp_path,
        env={},
        stdin=None,
        stdout=None,
        stderr=None,
        profile=profile,
        scope_name="test",
    )

    seatbelt = _seatbelt_profile(request)
    workspace = str(tmp_path).replace("\\", "\\\\").replace('"', '\\"')
    protected_path = str(protected).replace("\\", "\\\\").replace('"', '\\"')

    assert "(deny default)" in seatbelt
    assert "(allow file-read*)" in seatbelt
    assert f'(allow file-write* (subpath "{workspace}"))' in seatbelt
    assert f'(deny file-write* (subpath "{protected_path}"))' in seatbelt
    assert "(allow network*)" not in seatbelt


def test_sandbox_denial_allows_one_explicit_unsandboxed_retry(tmp_path):
    tool = RetryTool()

    async def run():
        emitted = []
        approval_ready = asyncio.Event()

        async def emit(event):
            emitted.append(event)
            if event["type"] == "tool_approval_required":
                approval_ready.set()

        cancellation = CancellationToken()
        broker = ApprovalBroker(
            run_id="run",
            session_id="session",
            emit=emit,
            cancellation=cancellation,
        )
        profile = compile_permission_profile(
            "default",
            workspace=tmp_path,
            sensitive_paths=(),
            temporary_paths=(tmp_path,),
            runtime_paths=(),
        )
        orchestrator = ToolExecutionOrchestrator(
            approval_broker=broker,
            permission_profile=profile,
        )
        task = asyncio.create_task(
            orchestrator.execute(
                router=retry_router(tool),
                tool_name=tool.name,
                raw_arguments={},
                context=ToolExecutionContext(
                    run_id="run",
                    session_id="session",
                    tool_call_id="call",
                    workspace=str(tmp_path),
                    mode="act",
                    cancellation=cancellation,
                    emit_event=emit,
                ),
            )
        )
        await asyncio.wait_for(approval_ready.wait(), timeout=1)
        approval = next(
            event
            for event in emitted
            if event["type"] == "tool_approval_required"
        )
        broker.resolve(
            run_id="run",
            approval_id=approval["approval_id"],
            decision="allow_once",
        )
        return await task, emitted

    result, emitted = asyncio.run(run())

    assert result.success is True
    assert tool.enforcements == ["managed", "disabled"]
    assert sum(
        event["type"] == "sandbox_retry_started" for event in emitted
    ) == 1


def test_deny_read_profile_blocks_unsandboxed_retry(tmp_path):
    tool = RetryTool()

    async def run():
        emitted = []

        async def emit(event):
            emitted.append(event)

        cancellation = CancellationToken()
        profile = compile_permission_profile(
            "default",
            workspace=tmp_path,
            sensitive_paths=(tmp_path / "secret",),
            temporary_paths=(tmp_path,),
            runtime_paths=(),
        )
        broker = ApprovalBroker(
            run_id="run",
            session_id="session",
            emit=emit,
            cancellation=cancellation,
        )
        result = await ToolExecutionOrchestrator(
            approval_broker=broker,
            permission_profile=profile,
        ).execute(
            router=retry_router(tool),
            tool_name=tool.name,
            raw_arguments={},
            context=ToolExecutionContext(
                run_id="run",
                session_id="session",
                tool_call_id="call",
                workspace=str(tmp_path),
                mode="act",
                cancellation=cancellation,
                emit_event=emit,
            ),
        )
        return result, emitted

    result, emitted = asyncio.run(run())

    assert result.error_code == "sandbox_denied"
    assert tool.enforcements == ["managed"]
    assert any(event["type"] == "sandbox_retry_blocked" for event in emitted)
    assert not any(
        event["type"] == "tool_approval_required" for event in emitted
    )


@pytest.mark.skipif(
    os.name != "nt" or find_windows_sandbox_host() is None,
    reason="Windows sandbox host is unavailable",
)
def test_windows_appcontainer_blocks_escape_and_protected_metadata(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    protected = workspace / ".git"
    protected.mkdir()
    config = protected / "config"
    config.write_text("original", encoding="utf-8")
    profile = compile_permission_profile(
        "default",
        workspace=workspace,
        temporary_paths=(workspace,),
        runtime_paths=(),
    )

    async def command(script: str):
        process = await process_launcher.spawn(
            "cmd.exe",
            "/d",
            "/s",
            "/c",
            script,
            cwd=workspace,
            profile=profile,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
        return process.returncode, stdout, stderr

    async def run():
        inside = await command("echo inside>inside.txt")
        escaped = await command(f"echo outside>{outside / 'outside.txt'}")
        metadata = await command("echo changed>.git\\config")
        return inside, escaped, metadata

    inside, escaped, metadata = asyncio.run(run())

    assert inside[0] == 0
    assert escaped[0] != 0
    assert metadata[0] != 0
    assert (workspace / "inside.txt").exists()
    assert not (outside / "outside.txt").exists()
    assert config.read_text(encoding="utf-8") == "original"
