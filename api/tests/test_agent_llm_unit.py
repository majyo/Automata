import asyncio
import json

import pytest

from automata_api.agent import llm
from automata_api.config import AgentConfig


def agent_config() -> AgentConfig:
    return AgentConfig(
        api_key="test-key",
        base_url="https://provider.test/",
        model="unit-model",
        timeout_seconds=42.0,
        temperature=0.4,
    )


class FakeResponse:
    def __init__(self, *, status_code=200, payload=None, body=b""):
        self.status_code = status_code
        self.payload = payload
        self.body = body

    def json(self):
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload

    async def aread(self):
        return self.body


class FakeAsyncClient:
    response = FakeResponse(payload={})
    requests = []

    def __init__(self, timeout=None):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, headers=None, json=None):
        self.__class__.requests.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": self.timeout,
            }
        )
        return self.__class__.response


class FakeStreamResponse(FakeResponse):
    def __init__(self, *, status_code=200, body=b"", lines=()):
        super().__init__(status_code=status_code, body=body)
        self.lines = list(lines)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class FakeStreamingAsyncClient(FakeAsyncClient):
    stream_response = FakeStreamResponse()
    stream_requests = []

    def stream(self, method, url, headers=None, json=None):
        self.__class__.stream_requests.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": self.timeout,
            }
        )
        return self.__class__.stream_response


def configure_llm(monkeypatch):
    monkeypatch.setattr(llm, "get_agent_config", agent_config)


def test_create_llm_response_builds_payload_with_tools(monkeypatch):
    configure_llm(monkeypatch)
    calls = []

    async def fake_post_chat_completion(payload):
        calls.append(payload)
        return {
            "choices": [
                {
                    "message": {
                        "content": "hello",
                        "tool_calls": [],
                    }
                }
            ]
        }

    monkeypatch.setattr(llm, "post_chat_completion", fake_post_chat_completion)

    result = asyncio.run(
        llm.create_llm_response(
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "read_file"}}],
        )
    )

    assert result["content"] == "hello"
    assert calls == [
        {
            "model": "unit-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
            "temperature": 0.4,
            "tools": [{"type": "function", "function": {"name": "read_file"}}],
            "tool_choice": "auto",
        }
    ]


def test_create_llm_response_omits_tools_when_absent(monkeypatch):
    configure_llm(monkeypatch)
    calls = []

    async def fake_post_chat_completion(payload):
        calls.append(payload)
        return {"choices": [{"message": {"content": "plain"}}]}

    monkeypatch.setattr(llm, "post_chat_completion", fake_post_chat_completion)

    assert asyncio.run(llm.create_llm_response([]))["content"] == "plain"
    assert "tools" not in calls[0]
    assert "tool_choice" not in calls[0]


def test_post_chat_completion_sends_request_and_parses_json(monkeypatch):
    configure_llm(monkeypatch)
    FakeAsyncClient.response = FakeResponse(payload={"choices": []})
    FakeAsyncClient.requests = []
    monkeypatch.setattr(llm.httpx, "AsyncClient", FakeAsyncClient)

    payload = {"messages": []}
    result = asyncio.run(llm.post_chat_completion(payload))

    assert result == {"choices": []}
    request = FakeAsyncClient.requests[0]
    assert request["url"] == "https://provider.test/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer test-key"
    assert request["headers"]["Content-Type"] == "application/json"
    assert request["json"] == payload
    assert request["timeout"].read == 42.0


def test_post_chat_completion_raises_provider_error(monkeypatch):
    configure_llm(monkeypatch)
    FakeAsyncClient.response = FakeResponse(
        status_code=429,
        body=b'{"error": {"message": "rate limited"}}',
    )
    FakeAsyncClient.requests = []
    monkeypatch.setattr(llm.httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(llm.AgentProviderError, match="rate limited"):
        asyncio.run(llm.post_chat_completion({"messages": []}))


@pytest.mark.parametrize(
    "payload",
    [
        json.JSONDecodeError("bad", "not json", 0),
        ["not", "a", "dict"],
    ],
)
def test_post_chat_completion_rejects_invalid_json(monkeypatch, payload):
    configure_llm(monkeypatch)
    FakeAsyncClient.response = FakeResponse(payload=payload)
    monkeypatch.setattr(llm.httpx, "AsyncClient", FakeAsyncClient)

    with pytest.raises(llm.AgentProviderError, match="invalid JSON"):
        asyncio.run(llm.post_chat_completion({"messages": []}))


def test_stream_llm_response_yields_sse_chunks(monkeypatch):
    configure_llm(monkeypatch)
    FakeStreamingAsyncClient.stream_response = FakeStreamResponse(
        lines=[
            "",
            'data: {"choices": [{"delta": {"content": "hel"}}]}',
            'data: {"choices": [{"delta": {"content": "lo"}}]}',
            "data: [DONE]",
            'data: {"choices": [{"delta": {"content": "ignored"}}]}',
        ]
    )
    FakeStreamingAsyncClient.stream_requests = []
    monkeypatch.setattr(llm.httpx, "AsyncClient", FakeStreamingAsyncClient)

    async def collect():
        return [chunk async for chunk in llm.stream_llm_response([{"role": "user"}])]

    assert asyncio.run(collect()) == ["hel", "lo"]
    request = FakeStreamingAsyncClient.stream_requests[0]
    assert request["method"] == "POST"
    assert request["json"]["stream"] is True
    assert request["json"]["model"] == "unit-model"


def test_stream_chat_completion_builds_payload_with_tools(monkeypatch):
    configure_llm(monkeypatch)
    FakeStreamingAsyncClient.stream_response = FakeStreamResponse(
        lines=['data: {"choices": [{"delta": {"content": "ok"}}]}']
    )
    FakeStreamingAsyncClient.stream_requests = []
    monkeypatch.setattr(llm.httpx, "AsyncClient", FakeStreamingAsyncClient)

    async def collect():
        return [
            delta
            async for delta in llm.stream_chat_completion(
                [{"role": "user"}],
                tools=[{"type": "function", "function": {"name": "read_file"}}],
            )
        ]

    assert asyncio.run(collect()) == [{"content": "ok"}]
    request = FakeStreamingAsyncClient.stream_requests[0]
    assert request["json"]["tools"] == [
        {"type": "function", "function": {"name": "read_file"}}
    ]
    assert request["json"]["tool_choice"] == "auto"


def test_parse_stream_delta_extracts_reasoning_finish_and_tool_calls():
    parsed = llm.parse_stream_delta(
        json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "content": "hello",
                            "reasoning_content": "thinking",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_read",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path": ',
                                    },
                                },
                                {
                                    "index": 1,
                                    "function": {
                                        "name": "rg",
                                        "arguments": '{"pattern": "x"}',
                                    },
                                },
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        )
    )

    assert parsed == {
        "content": "hello",
        "reasoning_content": "thinking",
        "tool_calls": [
            {
                "index": 0,
                "id": "call_read",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": '},
            },
            {
                "index": 1,
                "function": {"name": "rg", "arguments": '{"pattern": "x"}'},
            },
        ],
        "finish_reason": "tool_calls",
    }


def test_assistant_stream_accumulator_merges_content_reasoning_and_tool_calls():
    accumulator = llm.AssistantStreamAccumulator()
    accumulator.add({"content": "hel", "reasoning_content": "think "})
    accumulator.add(
        {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_read",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": '},
                },
                {
                    "index": 1,
                    "function": {"name": "rg", "arguments": '{"pattern": "async"'},
                },
            ]
        }
    )
    accumulator.add(
        {
            "content": "lo",
            "reasoning_content": "more",
            "tool_calls": [
                {"index": 0, "function": {"arguments": '"README.md"}'}},
                {"index": 1, "function": {"arguments": "}"}},
            ],
        }
    )

    assert accumulator.message() == {
        "role": "assistant",
        "content": "hello",
        "reasoning_content": "think more",
        "tool_calls": [
            {
                "id": "call_read",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path": "README.md"}',
                },
            },
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "rg", "arguments": '{"pattern": "async"}'},
            },
        ],
    }


def test_stream_llm_response_raises_provider_error(monkeypatch):
    configure_llm(monkeypatch)
    FakeStreamingAsyncClient.stream_response = FakeStreamResponse(
        status_code=500,
        body=b'{"message": "provider down"}',
    )
    monkeypatch.setattr(llm.httpx, "AsyncClient", FakeStreamingAsyncClient)

    async def collect():
        return [chunk async for chunk in llm.stream_llm_response([])]

    with pytest.raises(llm.AgentProviderError, match="provider down"):
        asyncio.run(collect())


def test_parse_completion_message_normalizes_content_reasoning_and_tools():
    parsed = llm.parse_completion_message(
        {
            "choices": [
                {
                    "message": {
                        "content": "content",
                        "reasoning_content": "reasoning",
                        "tool_calls": [
                            {
                                "id": "",
                                "function": {
                                    "name": "read_file",
                                    "arguments": {"not": "string"},
                                },
                            },
                            {"function": {"name": ""}},
                            "invalid",
                        ],
                    }
                }
            ]
        }
    )

    assert parsed == {
        "role": "assistant",
        "content": "content",
        "reasoning_content": "reasoning",
        "tool_calls": [
            {
                "id": "call_0",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            }
        ],
    }


@pytest.mark.parametrize(
    "response, message",
    [
        ({}, "no choices"),
        ({"choices": []}, "no choices"),
        ({"choices": [{"message": "bad"}]}, "invalid message"),
    ],
)
def test_parse_completion_message_rejects_invalid_payload(response, message):
    with pytest.raises(llm.AgentProviderError, match=message):
        llm.parse_completion_message(response)


def test_normalize_tool_calls_and_stream_chunk_invalid_inputs():
    assert llm.normalize_tool_calls(None) == []
    assert llm.normalize_tool_calls([{"function": {"name": "tool", "arguments": "x"}}]) == [
        {
            "id": "call_0",
            "type": "function",
            "function": {"name": "tool", "arguments": "x"},
        }
    ]
    assert llm.parse_stream_chunk("not json") == ""
    assert llm.parse_stream_chunk('{"choices": []}') == ""
    assert llm.parse_stream_chunk('{"choices": [{"delta": "bad"}]}') == ""
    assert llm.parse_stream_chunk(
        '{"choices": [{"delta": {"content": "chunk"}}]}'
    ) == "chunk"


def test_provider_error_message_prefers_structured_messages():
    error_message = asyncio.run(
        llm.provider_error_message(
            FakeResponse(
                status_code=400,
                body=b'{"error": {"message": "nested provider error"}}',
            )
        )
    )
    top_level_message = asyncio.run(
        llm.provider_error_message(
            FakeResponse(status_code=401, body=b'{"message": "top level"}')
        )
    )
    plain_message = asyncio.run(
        llm.provider_error_message(
            FakeResponse(status_code=500, body=b"plain provider body")
        )
    )

    assert error_message == "LLM provider returned HTTP 400: nested provider error"
    assert top_level_message == "LLM provider returned HTTP 401: top level"
    assert plain_message == "LLM provider returned HTTP 500: plain provider body"


def test_llm_timeout_uses_config(monkeypatch):
    configure_llm(monkeypatch)

    timeout = llm.llm_timeout()

    assert timeout.connect == 10.0
    assert timeout.read == 42.0
    assert timeout.write == 30.0
