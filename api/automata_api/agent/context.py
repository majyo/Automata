import json
from typing import Any

from automata_api.agent import llm
from automata_api.agent.prompts import agent_system_prompt
from automata_api.agent.types import AgentContextStore, EventEmitter
from automata_api.config import MAX_CONTEXT_MESSAGES, ContextCompressionConfig


RAW_CONTEXT_TAIL_MESSAGES = 8


async def fetch_agent_context(
    *,
    emit_event: EventEmitter,
    session_id: str,
    store: AgentContextStore,
    compression_config: ContextCompressionConfig,
    system_prompt: str | None = None,
) -> list[dict[str, Any]]:
    if not compression_config.enabled:
        return fetch_recent_agent_context(
            session_id=session_id, store=store, system_prompt=system_prompt
        )

    system_message = {
        "role": "system",
        "content": system_prompt or agent_system_prompt(),
    }
    summary = store.fetch_context_summary(session_id)
    through_sequence = int(summary["through_sequence"]) if summary else 0
    rows = store.get_messages_after_sequence(session_id, through_sequence)
    messages = build_context_messages(system_message, summary, rows)

    if context_char_count(messages) <= compression_config.threshold_chars:
        return messages

    if len(rows) <= RAW_CONTEXT_TAIL_MESSAGES:
        return messages

    before_chars = context_char_count(messages)
    compress_rows = rows[:-RAW_CONTEXT_TAIL_MESSAGES]
    tail_rows = rows[-RAW_CONTEXT_TAIL_MESSAGES:]
    summary_content = await create_context_summary(
        title="Conversation history compression",
        existing_summary=summary["content"] if summary else "",
        content=history_rows_text(compress_rows),
        target_chars=compression_config.target_chars,
    )
    through_sequence = int(compress_rows[-1]["sequence"])
    stored_summary = store.upsert_context_summary(
        session_id=session_id,
        content=summary_content,
        through_sequence=through_sequence,
    )
    compressed_messages = build_context_messages(system_message, stored_summary, tail_rows)
    await send_context_compressed_event(
        emit_event=emit_event,
        scope="history",
        before_chars=before_chars,
        after_chars=context_char_count(compressed_messages),
        summary_chars=len(summary_content),
        compressed_messages=len(compress_rows),
        through_sequence=through_sequence,
    )
    return compressed_messages


def fetch_recent_agent_context(
    *,
    session_id: str,
    store: AgentContextStore,
    system_prompt: str | None = None,
) -> list[dict[str, Any]]:
    messages = [
        {
            "role": "system",
            "content": system_prompt or agent_system_prompt(),
        }
    ]

    for row in store.get_recent_messages(session_id, MAX_CONTEXT_MESSAGES):
        messages.append(message_from_row(row))

    return messages


def build_context_messages(
    system_message: dict[str, Any],
    summary: dict[str, Any] | None,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    messages = [system_message]
    if summary and summary.get("content"):
        messages.append(summary_message(summary["content"], summary["through_sequence"]))

    for row in rows:
        messages.append(message_from_row(row))

    return messages


def message_from_row(row: dict[str, Any]) -> dict[str, str]:
    role = "assistant" if row["role"] == "agent" else "user"
    return {"role": role, "content": row["content"]}


def summary_message(content: str, through_sequence: int) -> dict[str, str]:
    return {
        "role": "system",
        "content": (
            "Compressed conversation summary through visible message sequence "
            f"{through_sequence}:\n{content}"
        ),
    }


async def compress_loop_context_if_needed(
    *,
    emit_event: EventEmitter,
    messages: list[dict[str, Any]],
    compression_config: ContextCompressionConfig,
) -> list[dict[str, Any]]:
    if not compression_config.enabled:
        return messages

    before_chars = context_char_count(messages)
    if before_chars <= compression_config.threshold_chars:
        return messages

    start_index = latest_tool_protocol_start(messages)
    if start_index is None:
        return messages

    loop_messages = messages[start_index:]
    summary_content = await create_context_summary(
        title="Recent tool activity compression",
        existing_summary="",
        content=messages_text(loop_messages),
        target_chars=compression_config.target_chars,
    )
    compressed_messages = [
        *messages[:start_index],
        {
            "role": "system",
            "content": f"Compressed recent tool activity summary:\n{summary_content}",
        },
    ]
    await send_context_compressed_event(
        emit_event=emit_event,
        scope="loop",
        before_chars=before_chars,
        after_chars=context_char_count(compressed_messages),
        summary_chars=len(summary_content),
        compressed_messages=len(loop_messages),
        through_sequence=None,
    )
    return compressed_messages


def latest_tool_protocol_start(messages: list[dict[str, Any]]) -> int | None:
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.get("role") == "assistant" and message.get("tool_calls"):
            return index

    return None


async def create_context_summary(
    *,
    title: str,
    existing_summary: str,
    content: str,
    target_chars: int,
) -> str:
    summary_request = [
        {
            "role": "system",
            "content": (
                "You compress agent conversation context. Return only a concise "
                "structured summary. Preserve user goals, explicit requirements, "
                "completed and failed actions, file paths, commands, errors, "
                "decisions, pending work, uncertainty, and anything the agent "
                "must not claim as completed. Do not invent facts."
            ),
        },
        {
            "role": "user",
            "content": (
                f"{title}\nTarget maximum length: {target_chars} characters.\n\n"
                f"Existing summary:\n{existing_summary or '(none)'}\n\n"
                f"Content to compress:\n{content}"
            ),
        },
    ]
    response = await llm.create_llm_response(summary_request, tools=None)
    summary = response.get("content")
    if not isinstance(summary, str) or not summary.strip():
        raise llm.AgentProviderError("LLM provider returned an empty context summary.")

    return summary.strip()


def history_rows_text(rows: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        f"[sequence={row['sequence']} role={row['role']}]\n{row['content']}"
        for row in rows
    )


def messages_text(messages: list[dict[str, Any]]) -> str:
    return "\n\n".join(
        json.dumps(message, ensure_ascii=False, sort_keys=True) for message in messages
    )


def context_char_count(messages: list[dict[str, Any]]) -> int:
    return sum(len(json.dumps(message, ensure_ascii=False)) for message in messages)


async def send_context_compressed_event(
    *,
    emit_event: EventEmitter,
    scope: str,
    before_chars: int,
    after_chars: int,
    summary_chars: int,
    compressed_messages: int,
    through_sequence: int | None,
) -> None:
    event: dict[str, Any] = {
        "type": "context_compressed",
        "scope": scope,
        "before_chars": before_chars,
        "after_chars": after_chars,
        "summary_chars": summary_chars,
        "compressed_messages": compressed_messages,
    }
    if through_sequence is not None:
        event["through_sequence"] = through_sequence

    await emit_event(event)

