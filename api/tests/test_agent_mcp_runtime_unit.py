from __future__ import annotations

import asyncio
import json
import sys

from automata_api.agent.backends.local import LocalBackend
from automata_api.agent.mcp.config import load_mcp_config
from automata_api.agent.mcp.runtime import create_mcp_tool_runtime
from automata_api.agent.mcp.trust import McpTrustStore, create_grant


def write_workspace_server(workspace, *, command, args):
    config = workspace / ".automata" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "workspace-server": {
                        "transport": {
                            "type": "stdio",
                            "command": command,
                            "args": args,
                            "cwd": "${workspace}",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def visible_names(runtime):
    return {
        spec["function"]["name"]
        for spec in runtime.router.model_visible_specs(mode="act")
    }


def test_ungranted_workspace_server_is_only_a_candidate_and_never_starts(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    marker = tmp_path / "started.txt"
    script = tmp_path / "must-not-run.py"
    script.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('started')\n",
        encoding="utf-8",
    )
    write_workspace_server(
        workspace,
        command=sys.executable,
        args=[str(script)],
    )
    store = McpTrustStore(tmp_path / "mcp-grants.json")

    async def run():
        async with create_mcp_tool_runtime(
            backend=LocalBackend(str(workspace)),
            session_id="session",
            workspace=str(workspace),
            mode="act",
            trust_store=store,
        ) as runtime:
            assert [candidate.name for candidate in runtime.candidates] == [
                "workspace-server"
            ]
            assert "read_file" in visible_names(runtime)

    asyncio.run(run())
    assert not marker.exists()


def test_granted_server_start_failure_keeps_backend_tools_available(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_workspace_server(
        workspace,
        command="automata-command-that-does-not-exist",
        args=[],
    )
    definition = load_mcp_config(
        str(workspace),
        data_dir=tmp_path / "data",
        environ={},
    ).definitions[0]
    store = McpTrustStore(tmp_path / "mcp-grants.json")
    store.save(
        create_grant(
            definition,
            str(workspace),
            connection="allow",
            trust="trusted",
            default_call_policy="allow",
        )
    )

    async def run():
        async with create_mcp_tool_runtime(
            backend=LocalBackend(str(workspace)),
            session_id="session",
            workspace=str(workspace),
            mode="act",
            trust_store=store,
        ) as runtime:
            names = visible_names(runtime)
            assert "read_file" in names
            assert not any(name.startswith("mcp__") for name in names)

    asyncio.run(run())
