from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from automata_api.agent.mcp.config import (
    McpServerDefinition,
    McpStreamableHttpTransportDefinition,
)
from automata_api.agent.mcp.manager import McpConnectionManager
from automata_api.agent.mcp.schema import McpError


FIXTURE = Path(__file__).parent / "fixtures" / "fake_mcp_http_server.py"


def unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_until_listening(process: subprocess.Popen, port: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("fake Streamable HTTP server exited during startup")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError("fake Streamable HTTP server did not start")


@pytest.mark.parametrize("json_response", [True, False], ids=["json", "sse"])
def test_official_sdk_streamable_http_lists_calls_reuses_session_and_deletes(
    tmp_path, monkeypatch, json_response
):
    port = unused_port()
    request_log = tmp_path / "requests.jsonl"
    call_log = tmp_path / "call.json"
    token = "integration-secret"
    environment = {
        **os.environ,
        "FAKE_MCP_HTTP_PORT": str(port),
        "FAKE_MCP_HTTP_REQUEST_LOG": str(request_log),
        "FAKE_MCP_HTTP_CALL_LOG": str(call_log),
        "FAKE_MCP_HTTP_JSON_RESPONSE": "1" if json_response else "0",
    }
    process = subprocess.Popen(
        [sys.executable, str(FIXTURE)],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    monkeypatch.setenv("MCP_HTTP_TEST_TOKEN", token)
    definition = McpServerDefinition(
        name="fake-http",
        transport=McpStreamableHttpTransportDefinition(
            url=f"http://127.0.0.1:{port}/mcp",
            headers={"Authorization": "Bearer ${MCP_HTTP_TEST_TOKEN}"},
        ),
        provenance="user",
        source_path="mcp.json",
    )

    async def run():
        async with McpConnectionManager(
            (definition,),
            str(tmp_path),
        ) as manager:
            tools = await manager.list_tools("fake-http")
            assert [tool.name for tool in tools] == ["echo_remote"]
            result = await manager.call_tool(
                "fake-http",
                "echo_remote",
                {"value": "from-http"},
            )
            assert result.is_error is False
            assert result.structured_content == {"echo": "from-http"}

    try:
        wait_until_listening(process, port)
        asyncio.run(run())
    finally:
        process.terminate()
        process.wait(timeout=10)

    assert json.loads(call_log.read_text(encoding="utf-8")) == {
        "name": "echo_remote",
        "arguments": {"value": "from-http"},
    }
    requests = [
        json.loads(line)
        for line in request_log.read_text(encoding="utf-8").splitlines()
    ]
    assert all(item["authorization"] == f"Bearer {token}" for item in requests)
    assert any(item["method"] == "DELETE" for item in requests)
    session_ids = {
        item["session_id"] for item in requests if item["session_id"] is not None
    }
    assert len(session_ids) == 1
    assert any(item["protocol_version"] == "2025-11-25" for item in requests)


def test_streamable_http_does_not_follow_redirects(tmp_path):
    port = unused_port()
    request_log = tmp_path / "redirect-requests.jsonl"
    call_log = tmp_path / "redirect-call.json"
    process = subprocess.Popen(
        [sys.executable, str(FIXTURE)],
        env={
            **os.environ,
            "FAKE_MCP_HTTP_PORT": str(port),
            "FAKE_MCP_HTTP_REQUEST_LOG": str(request_log),
            "FAKE_MCP_HTTP_CALL_LOG": str(call_log),
            "FAKE_MCP_HTTP_JSON_RESPONSE": "1",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    definition = McpServerDefinition(
        name="redirecting-http",
        transport=McpStreamableHttpTransportDefinition(
            url=f"http://127.0.0.1:{port}/redirect",
        ),
        provenance="user",
        source_path="mcp.json",
    )

    async def run():
        async with McpConnectionManager(
            (definition,),
            str(tmp_path),
        ) as manager:
            with pytest.raises(McpError) as captured:
                await manager.list_tools("redirecting-http")
            assert captured.value.code == "mcp_server_unavailable"

    try:
        wait_until_listening(process, port)
        asyncio.run(run())
    finally:
        process.terminate()
        process.wait(timeout=10)

    requests = [
        json.loads(line)
        for line in request_log.read_text(encoding="utf-8").splitlines()
    ]
    assert {item["path"] for item in requests} == {"/redirect"}
    assert not call_log.exists()
