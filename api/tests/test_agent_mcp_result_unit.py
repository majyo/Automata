from __future__ import annotations

import json

from automata_api.agent.mcp.result import (
    MAX_TEXT_CHARS,
    mcp_result_to_tool_result,
)
from automata_api.agent.mcp.schema import McpCallResult, McpToolMetadata


def metadata():
    return McpToolMetadata(
        alias="mcp__server__tool__12345678",
        server_name="server",
        server_fingerprint="a" * 64,
        original_name="tool",
        title="Tool",
        description="Tool description",
        input_schema={"type": "object"},
        output_schema=None,
        read_only=True,
        destructive=False,
        idempotent=False,
        open_world=False,
        remote=False,
        credentialed=False,
        trusted_server=True,
    )


def convert(result):
    converted = mcp_result_to_tool_result(
        metadata=metadata(),
        arguments={},
        result=result,
        duration_seconds=0.01,
    )
    return converted, json.loads(converted.content)


def test_binary_content_is_omitted_and_resource_uris_are_filtered():
    converted, payload = convert(
        McpCallResult(
            content=(
                {
                    "type": "image",
                    "mimeType": "image/png",
                    "data": "secret-base64",
                },
                {
                    "type": "audio",
                    "mimeType": "audio/wav",
                    "data": "audio-base64",
                },
                {
                    "type": "resource_link",
                    "uri": "ftp://unsafe.example/file",
                    "name": "unsafe",
                },
                {
                    "type": "resource_link",
                    "uri": "https://safe.example/file",
                    "name": "safe",
                },
            )
        )
    )

    serialized = converted.content
    assert "secret-base64" not in serialized
    assert "audio-base64" not in serialized
    assert "ftp://unsafe.example/file" not in serialized
    assert "https://safe.example/file" in serialized
    assert payload["truncated"] is True
    assert payload["content"][0]["data_omitted"] is True
    assert payload["content"][1]["data_omitted"] is True


def test_text_result_is_bounded_before_entering_model_context():
    _, payload = convert(
        McpCallResult(
            content=(
                {"type": "text", "text": "x" * (MAX_TEXT_CHARS + 100)},
            )
        )
    )

    assert len(payload["text"]) == MAX_TEXT_CHARS
    assert len(payload["content"][0]["text"]) == MAX_TEXT_CHARS
    assert payload["truncated"] is True
