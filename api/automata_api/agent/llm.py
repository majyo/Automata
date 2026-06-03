import json
from collections.abc import AsyncIterator
from typing import Any, TypedDict

import httpx

from automata_api.config import get_agent_config


class AgentProviderError(RuntimeError):
    pass


class LLMToolCallDelta(TypedDict, total=False):
    index: int
    id: str
    type: str
    function: dict[str, str]


class LLMStreamDelta(TypedDict, total=False):
    content: str
    reasoning_content: str
    tool_calls: list[LLMToolCallDelta]
    finish_reason: str | None


async def create_llm_response(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    config = get_agent_config()
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "stream": False,
        "temperature": config.temperature,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    response = await post_chat_completion(payload)
    return parse_completion_message(response)


async def stream_llm_response(messages: list[dict[str, Any]]) -> AsyncIterator[str]:
    """Compatibility wrapper for callers that only need streamed content text."""
    async for delta in stream_chat_completion(messages):
        content = delta.get("content")
        if isinstance(content, str) and content:
            yield content


async def stream_chat_completion(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
) -> AsyncIterator[LLMStreamDelta]:
    config = get_agent_config()
    url = f"{config.base_url.rstrip('/')}/chat/completions"
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "stream": True,
        "temperature": config.temperature,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    async with httpx.AsyncClient(timeout=llm_timeout()) as client:
        async with client.stream(
            "POST",
            url,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        ) as response:
            if response.status_code >= 400:
                raise AgentProviderError(await provider_error_message(response))

            async for line in response.aiter_lines():
                line = line.strip()
                if not line:
                    continue

                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    break

                delta = parse_stream_delta(data)
                if delta:
                    yield delta


async def post_chat_completion(payload: dict[str, Any]) -> dict[str, Any]:
    config = get_agent_config()
    url = f"{config.base_url.rstrip('/')}/chat/completions"
    timeout = llm_timeout()

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if response.status_code >= 400:
            raise AgentProviderError(await provider_error_message(response))

        try:
            payload = response.json()
        except json.JSONDecodeError as error:
            raise AgentProviderError("LLM provider returned invalid JSON.") from error

        if not isinstance(payload, dict):
            raise AgentProviderError("LLM provider returned invalid JSON.")

        return payload


def llm_timeout() -> httpx.Timeout:
    config = get_agent_config()
    timeout = httpx.Timeout(
        timeout=config.timeout_seconds,
        connect=10.0,
        read=config.timeout_seconds,
        write=30.0,
        pool=10.0,
    )
    return timeout


def parse_completion_message(response: dict[str, Any]) -> dict[str, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AgentProviderError("LLM provider returned no choices.")

    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise AgentProviderError("LLM provider returned an invalid message.")

    content = message.get("content")
    reasoning_content = message.get("reasoning_content")
    tool_calls = message.get("tool_calls")
    parsed_message: dict[str, Any] = {
        "role": "assistant",
        "content": content if isinstance(content, str) else "",
        "tool_calls": normalize_tool_calls(tool_calls),
    }

    if isinstance(reasoning_content, str):
        parsed_message["reasoning_content"] = reasoning_content

    return parsed_message


def normalize_tool_calls(raw_tool_calls: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_tool_calls, list):
        return []

    normalized: list[dict[str, Any]] = []
    for index, tool_call in enumerate(raw_tool_calls):
        if not isinstance(tool_call, dict):
            continue

        function = tool_call.get("function")
        if not isinstance(function, dict):
            continue

        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            continue

        arguments = function.get("arguments")
        if not isinstance(arguments, str):
            arguments = "{}"

        call_id = tool_call.get("id")
        if not isinstance(call_id, str) or not call_id.strip():
            call_id = f"call_{index}"

        normalized.append(
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments,
                },
            }
        )

    return normalized


class AssistantStreamAccumulator:
    def __init__(self) -> None:
        self._content_parts: list[str] = []
        self._reasoning_parts: list[str] = []
        self._tool_calls: dict[int, dict[str, Any]] = {}

    def add(self, delta: LLMStreamDelta) -> None:
        content = delta.get("content")
        if isinstance(content, str):
            self._content_parts.append(content)

        reasoning_content = delta.get("reasoning_content")
        if isinstance(reasoning_content, str):
            self._reasoning_parts.append(reasoning_content)

        for tool_call_delta in delta.get("tool_calls", []):
            index = tool_call_delta.get("index")
            if not isinstance(index, int):
                continue

            tool_call = self._tool_calls.setdefault(
                index,
                {
                    "id": "",
                    "type": "function",
                    "function": {"name": "", "arguments": ""},
                },
            )

            call_id = tool_call_delta.get("id")
            if isinstance(call_id, str) and call_id:
                tool_call["id"] = call_id

            call_type = tool_call_delta.get("type")
            if isinstance(call_type, str) and call_type:
                tool_call["type"] = call_type

            function_delta = tool_call_delta.get("function")
            if not isinstance(function_delta, dict):
                continue

            function = tool_call["function"]
            name = function_delta.get("name")
            if isinstance(name, str) and name:
                function["name"] = f"{function.get('name', '')}{name}"

            arguments = function_delta.get("arguments")
            if isinstance(arguments, str):
                function["arguments"] = (
                    f"{function.get('arguments', '')}{arguments}"
                )

    def message(self) -> dict[str, Any]:
        content = "".join(self._content_parts)
        raw_tool_calls = [
            {
                "id": tool_call["id"] or f"call_{index}",
                "type": tool_call.get("type") or "function",
                "function": {
                    "name": tool_call["function"].get("name", ""),
                    "arguments": tool_call["function"].get("arguments", "{}"),
                },
            }
            for index, tool_call in sorted(self._tool_calls.items())
        ]
        message: dict[str, Any] = {
            "role": "assistant",
            "content": content,
            "tool_calls": normalize_tool_calls(raw_tool_calls),
        }

        reasoning_content = "".join(self._reasoning_parts)
        if reasoning_content:
            message["reasoning_content"] = reasoning_content

        return message


def parse_stream_chunk(data: str) -> str:
    delta = parse_stream_delta(data)
    content = delta.get("content")
    return content if isinstance(content, str) else ""


def parse_stream_delta(data: str) -> LLMStreamDelta:
    try:
        event = json.loads(data)
    except json.JSONDecodeError:
        return {}

    choices = event.get("choices")
    if not isinstance(choices, list) or not choices:
        return {}

    choice = choices[0]
    if not isinstance(choice, dict):
        return {}

    raw_delta = choice.get("delta")
    if not isinstance(raw_delta, dict):
        raw_delta = {}

    parsed: LLMStreamDelta = {}
    content = raw_delta.get("content")
    if isinstance(content, str):
        parsed["content"] = content

    reasoning_content = raw_delta.get("reasoning_content")
    if isinstance(reasoning_content, str):
        parsed["reasoning_content"] = reasoning_content

    tool_calls = parse_tool_call_deltas(raw_delta.get("tool_calls"))
    if tool_calls:
        parsed["tool_calls"] = tool_calls

    finish_reason = choice.get("finish_reason")
    if isinstance(finish_reason, str) or finish_reason is None:
        if finish_reason is not None:
            parsed["finish_reason"] = finish_reason

    return parsed


def parse_tool_call_deltas(raw_tool_calls: Any) -> list[LLMToolCallDelta]:
    if not isinstance(raw_tool_calls, list):
        return []

    parsed: list[LLMToolCallDelta] = []
    for fallback_index, raw_tool_call in enumerate(raw_tool_calls):
        if not isinstance(raw_tool_call, dict):
            continue

        raw_index = raw_tool_call.get("index")
        index = raw_index if isinstance(raw_index, int) else fallback_index
        tool_call_delta: LLMToolCallDelta = {"index": index}

        call_id = raw_tool_call.get("id")
        if isinstance(call_id, str):
            tool_call_delta["id"] = call_id

        call_type = raw_tool_call.get("type")
        if isinstance(call_type, str):
            tool_call_delta["type"] = call_type

        raw_function = raw_tool_call.get("function")
        if isinstance(raw_function, dict):
            function_delta: dict[str, str] = {}
            name = raw_function.get("name")
            if isinstance(name, str):
                function_delta["name"] = name

            arguments = raw_function.get("arguments")
            if isinstance(arguments, str):
                function_delta["arguments"] = arguments

            if function_delta:
                tool_call_delta["function"] = function_delta

        if set(tool_call_delta) != {"index"}:
            parsed.append(tool_call_delta)

    return parsed


async def provider_error_message(response: httpx.Response) -> str:
    body = (await response.aread()).decode("utf-8", errors="replace")
    message = body.strip()

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = None

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            message = error["message"]
        elif isinstance(payload.get("message"), str):
            message = payload["message"]

    return f"LLM provider returned HTTP {response.status_code}: {message[:500]}"
