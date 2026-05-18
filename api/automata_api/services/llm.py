import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from automata_api.config import get_agent_config


class AgentProviderError(RuntimeError):
    pass


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
    config = get_agent_config()
    url = f"{config.base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": config.model,
        "messages": messages,
        "stream": True,
        "temperature": config.temperature,
    }

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

                chunk = parse_stream_chunk(data)
                if chunk:
                    yield chunk


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
    tool_calls = message.get("tool_calls")
    return {
        "role": "assistant",
        "content": content if isinstance(content, str) else "",
        "tool_calls": normalize_tool_calls(tool_calls),
    }


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


def parse_stream_chunk(data: str) -> str:
    try:
        event = json.loads(data)
    except json.JSONDecodeError:
        return ""

    choices = event.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""

    delta = choices[0].get("delta")
    if not isinstance(delta, dict):
        return ""

    content = delta.get("content")
    return content if isinstance(content, str) else ""


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
