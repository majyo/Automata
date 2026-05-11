import json
from collections.abc import AsyncIterator

import httpx

from automata_api.config import get_agent_config


class AgentProviderError(RuntimeError):
    pass


async def stream_llm_response(messages: list[dict[str, str]]) -> AsyncIterator[str]:
    config = get_agent_config()
    url = f"{config.base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": config.model,
        "messages": messages,
        "stream": True,
        "temperature": config.temperature,
    }
    timeout = httpx.Timeout(
        timeout=config.timeout_seconds,
        connect=10.0,
        read=config.timeout_seconds,
        write=30.0,
        pool=10.0,
    )

    async with httpx.AsyncClient(timeout=timeout) as client:
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
