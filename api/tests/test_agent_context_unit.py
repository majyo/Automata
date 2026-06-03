import asyncio
import json

import pytest

from automata_api.agent import context, llm
from automata_api.config import ContextCompressionConfig


class MemoryStore:
    def __init__(
        self,
        *,
        recent_messages=None,
        rows_after_sequence=None,
        summary=None,
    ):
        self.recent_messages = recent_messages or []
        self.rows_after_sequence = rows_after_sequence or []
        self.summary = summary
        self.upserts = []
        self.calls = []

    def get_recent_messages(self, session_id: str, limit: int) -> list[dict]:
        self.calls.append(("recent", session_id, limit))
        return self.recent_messages[-limit:]

    def get_messages_after_sequence(self, session_id: str, sequence: int) -> list[dict]:
        self.calls.append(("after", session_id, sequence))
        return [
            row
            for row in self.rows_after_sequence
            if int(row["sequence"]) > sequence
        ]

    def fetch_context_summary(self, session_id: str) -> dict | None:
        self.calls.append(("summary", session_id))
        return self.summary

    def upsert_context_summary(
        self, session_id: str, content: str, through_sequence: int
    ) -> dict:
        stored = {
            "session_id": session_id,
            "content": content,
            "through_sequence": through_sequence,
        }
        self.upserts.append(stored)
        self.summary = stored
        return stored


class EventRecorder:
    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)


def rows(count: int, *, content_size: int = 8) -> list[dict]:
    return [
        {
            "role": "agent" if index % 2 else "user",
            "content": f"message-{index} " + ("x" * content_size),
            "sequence": index + 1,
        }
        for index in range(count)
    ]


def test_fetch_recent_agent_context_uses_recent_store_and_role_mapping():
    store = MemoryStore(
        recent_messages=[
            {"role": "user", "content": "hello"},
            {"role": "agent", "content": "hi"},
        ]
    )

    messages = context.fetch_recent_agent_context(
        session_id="session-1",
        store=store,
        system_prompt="custom system",
    )

    assert messages == [
        {"role": "system", "content": "custom system"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    assert store.calls == [("recent", "session-1", 24)]


def test_build_context_messages_injects_existing_summary():
    built = context.build_context_messages(
        {"role": "system", "content": "system"},
        {"content": "prior summary", "through_sequence": 7},
        [{"role": "agent", "content": "reply", "sequence": 8}],
    )

    assert built[0] == {"role": "system", "content": "system"}
    assert built[1] == context.summary_message("prior summary", 7)
    assert built[2] == {"role": "assistant", "content": "reply"}


def test_fetch_agent_context_skips_compression_when_disabled(monkeypatch):
    async def fail_create_context_summary(*args, **kwargs):
        raise AssertionError("compression should be skipped")

    monkeypatch.setattr(context, "create_context_summary", fail_create_context_summary)

    store = MemoryStore(recent_messages=[{"role": "user", "content": "recent"}])
    recorder = EventRecorder()
    messages = asyncio.run(
        context.fetch_agent_context(
            emit_event=recorder.emit,
            session_id="session-1",
            store=store,
            compression_config=ContextCompressionConfig(False, 1, 1),
        )
    )

    assert messages[-1] == {"role": "user", "content": "recent"}
    assert store.calls == [("recent", "session-1", 24)]
    assert recorder.events == []


def test_fetch_agent_context_keeps_uncompressed_messages_below_threshold(monkeypatch):
    async def fail_create_context_summary(*args, **kwargs):
        raise AssertionError("compression should be skipped")

    monkeypatch.setattr(context, "create_context_summary", fail_create_context_summary)

    store = MemoryStore(rows_after_sequence=rows(2))
    recorder = EventRecorder()
    messages = asyncio.run(
        context.fetch_agent_context(
            emit_event=recorder.emit,
            session_id="session-1",
            store=store,
            compression_config=ContextCompressionConfig(True, 100_000, 1_000),
            system_prompt="system",
        )
    )

    assert [message["content"] for message in messages] == [
        "system",
        "message-0 xxxxxxxx",
        "message-1 xxxxxxxx",
    ]
    assert store.upserts == []
    assert recorder.events == []


def test_fetch_agent_context_skips_history_compression_when_tail_too_short(monkeypatch):
    async def fail_create_context_summary(*args, **kwargs):
        raise AssertionError("compression should be skipped")

    monkeypatch.setattr(context, "create_context_summary", fail_create_context_summary)

    store = MemoryStore(rows_after_sequence=rows(3, content_size=200))
    recorder = EventRecorder()
    messages = asyncio.run(
        context.fetch_agent_context(
            emit_event=recorder.emit,
            session_id="session-1",
            store=store,
            compression_config=ContextCompressionConfig(True, 1, 100),
            system_prompt="system",
        )
    )

    assert len(messages) == 4
    assert store.upserts == []
    assert recorder.events == []


def test_fetch_agent_context_compresses_history_and_persists_summary(monkeypatch):
    async def fake_create_context_summary(
        *, title, existing_summary, content, target_chars
    ):
        assert title == "Conversation history compression"
        assert existing_summary == "old summary"
        assert "[sequence=2 role=agent]" in content
        assert target_chars == 150
        return "new summary"

    monkeypatch.setattr(context, "create_context_summary", fake_create_context_summary)

    store = MemoryStore(
        rows_after_sequence=rows(10, content_size=220),
        summary={"content": "old summary", "through_sequence": 0},
    )
    recorder = EventRecorder()
    messages = asyncio.run(
        context.fetch_agent_context(
            emit_event=recorder.emit,
            session_id="session-1",
            store=store,
            compression_config=ContextCompressionConfig(True, 1_200, 150),
            system_prompt="system",
        )
    )

    assert store.upserts == [
        {
            "session_id": "session-1",
            "content": "new summary",
            "through_sequence": 2,
        }
    ]
    assert messages[1] == context.summary_message("new summary", 2)
    assert messages[2]["content"].startswith("message-2 ")
    assert recorder.events[0]["type"] == "context_compressed"
    assert recorder.events[0]["scope"] == "history"
    assert recorder.events[0]["compressed_messages"] == 2
    assert recorder.events[0]["through_sequence"] == 2


def test_compress_loop_context_skips_when_disabled_or_under_threshold(monkeypatch):
    async def fail_create_context_summary(*args, **kwargs):
        raise AssertionError("compression should be skipped")

    monkeypatch.setattr(context, "create_context_summary", fail_create_context_summary)

    messages = [{"role": "system", "content": "system"}]
    recorder = EventRecorder()
    disabled = asyncio.run(
        context.compress_loop_context_if_needed(
            emit_event=recorder.emit,
            messages=messages,
            compression_config=ContextCompressionConfig(False, 1, 1),
        )
    )
    under_threshold = asyncio.run(
        context.compress_loop_context_if_needed(
            emit_event=recorder.emit,
            messages=messages,
            compression_config=ContextCompressionConfig(True, 10_000, 1),
        )
    )

    assert disabled is messages
    assert under_threshold is messages
    assert recorder.events == []


def test_compress_loop_context_skips_without_tool_protocol(monkeypatch):
    async def fail_create_context_summary(*args, **kwargs):
        raise AssertionError("compression should be skipped")

    monkeypatch.setattr(context, "create_context_summary", fail_create_context_summary)

    messages = [
        {"role": "system", "content": "system"},
        {"role": "assistant", "content": "x" * 2_000},
    ]
    recorder = EventRecorder()
    compressed = asyncio.run(
        context.compress_loop_context_if_needed(
            emit_event=recorder.emit,
            messages=messages,
            compression_config=ContextCompressionConfig(True, 1, 100),
        )
    )

    assert compressed is messages
    assert recorder.events == []


def test_compress_loop_context_replaces_latest_tool_protocol(monkeypatch):
    async def fake_create_context_summary(*, title, existing_summary, content, target_chars):
        assert title == "Recent tool activity compression"
        assert existing_summary == ""
        assert "call_latest" in content
        assert "call_old" not in content
        assert target_chars == 80
        return "loop summary"

    monkeypatch.setattr(context, "create_context_summary", fake_create_context_summary)

    messages = [
        {"role": "system", "content": "system"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_old"}],
        },
        {"role": "tool", "tool_call_id": "call_old", "content": "old"},
        {"role": "user", "content": "again"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_latest"}],
        },
        {"role": "tool", "tool_call_id": "call_latest", "content": "x" * 2_000},
    ]
    recorder = EventRecorder()
    compressed = asyncio.run(
        context.compress_loop_context_if_needed(
            emit_event=recorder.emit,
            messages=messages,
            compression_config=ContextCompressionConfig(True, 1, 80),
        )
    )

    assert compressed[:4] == messages[:4]
    assert compressed[-1] == {
        "role": "system",
        "content": "Compressed recent tool activity summary:\nloop summary",
    }
    assert recorder.events[0]["scope"] == "loop"
    assert recorder.events[0]["compressed_messages"] == 2
    assert context.latest_tool_protocol_start(messages) == 4


def test_create_context_summary_strips_content_and_rejects_empty(monkeypatch):
    calls = []

    async def fake_create_llm_response(messages, tools=None):
        calls.append({"messages": messages, "tools": tools})
        return {"role": "assistant", "content": "  compact summary  "}

    monkeypatch.setattr(llm, "create_llm_response", fake_create_llm_response)

    summary = asyncio.run(
        context.create_context_summary(
            title="Title",
            existing_summary="old",
            content="content",
            target_chars=25,
        )
    )

    assert summary == "compact summary"
    assert calls[0]["tools"] is None
    assert "Target maximum length: 25 characters." in calls[0]["messages"][1]["content"]

    async def empty_response(messages, tools=None):
        return {"role": "assistant", "content": " "}

    monkeypatch.setattr(llm, "create_llm_response", empty_response)
    with pytest.raises(llm.AgentProviderError, match="empty context summary"):
        asyncio.run(
            context.create_context_summary(
                title="Title",
                existing_summary="",
                content="content",
                target_chars=25,
            )
        )


def test_context_formatting_helpers():
    history = context.history_rows_text(
        [{"sequence": 3, "role": "user", "content": "hello"}]
    )
    serialized = context.messages_text(
        [{"role": "user", "content": "hello", "z": 1}]
    )
    event_recorder = EventRecorder()

    asyncio.run(
        context.send_context_compressed_event(
            emit_event=event_recorder.emit,
            scope="history",
            before_chars=100,
            after_chars=40,
            summary_chars=20,
            compressed_messages=3,
            through_sequence=None,
        )
    )

    assert history == "[sequence=3 role=user]\nhello"
    assert json.loads(serialized) == {"content": "hello", "role": "user", "z": 1}
    assert context.context_char_count([{"role": "user", "content": "hi"}]) > 0
    assert context.latest_tool_protocol_start([{"role": "assistant"}]) is None
    assert event_recorder.events == [
        {
            "type": "context_compressed",
            "scope": "history",
            "before_chars": 100,
            "after_chars": 40,
            "summary_chars": 20,
            "compressed_messages": 3,
        }
    ]
