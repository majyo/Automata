import asyncio
import json

from automata_api.agent.backends.local import LocalBackend
from automata_api.agent.execution.model import CancellationToken
from automata_api.agent.runtime import stream_model_loop
from automata_api.agent.tools.model import ToolDiscoveryContext
from automata_api.agent.tools.providers import ContextToolProvider
from automata_api.agent.tools.router import ToolRouter
from automata_api.agent.tools.thread_context import (
    SEARCH_THREAD_CONTEXT_NAME,
    SearchThreadContextTool,
)
from automata_api.agent.types import AgentLoopEvent
from automata_api.config import ContextCompressionConfig
from automata_api.db.connection import connect_db, db_lock
from automata_api.db.context_search import CONTEXT_SOURCE_SEARCH
from automata_api.repositories.agent_store import SessionAgentContextStore
from automata_api.repositories.sessions import save_context_message, search_context


def test_context_search_returns_old_messages_and_isolates_sessions(client):
    first = client.post("/sessions", json={"title": "First"}).json()
    second = client.post("/sessions", json={"title": "Second"}).json()

    save_context_message(
        first["id"],
        {"role": "user", "content": "The durable retrieval token is alpha-needle."},
    )
    save_context_message(
        second["id"],
        {"role": "user", "content": "The other session token is beta-needle."},
    )

    result = search_context(first["id"], "alpha-needle")
    assert result["returned"] == 1
    assert result["matches"][0]["role"] == "user"
    assert "alpha-needle" in result["matches"][0]["content"]

    isolated = search_context(first["id"], "beta-needle")
    assert isolated["matches"] == []


def test_context_search_filters_tool_results_and_search_results(client):
    session = client.post("/sessions", json={"title": "Search filters"}).json()

    save_context_message(
        session["id"],
        {"role": "tool", "content": "tool-output-needle"},
    )
    save_context_message(
        session["id"],
        {"role": "tool", "content": "self-index-needle"},
        source=CONTEXT_SOURCE_SEARCH,
    )

    assert search_context(session["id"], "tool-output-needle")["returned"] == 1
    assert (
        search_context(
            session["id"],
            "tool-output-needle",
            include_tool_results=False,
        )["returned"]
        == 0
    )
    assert search_context(session["id"], "self-index-needle")["returned"] == 0


def test_session_delete_removes_context_search_documents(client):
    session = client.post("/sessions", json={"title": "Delete search"}).json()
    save_context_message(
        session["id"],
        {"role": "user", "content": "delete-search-needle"},
    )

    assert client.delete(f"/sessions/{session['id']}").status_code == 204

    with db_lock, connect_db() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM agent_context_search_documents"
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT COUNT(*) FROM agent_context_search_fts"
        ).fetchone()[0] == 0


def test_context_search_handles_chinese_and_result_bounds(client):
    session = client.post("/sessions", json={"title": "Chinese search"}).json()
    long_content = "前置内容。" + ("x" * 4_500) + " 中文检索唯一词。"
    save_context_message(
        session["id"],
        {"role": "assistant", "content": long_content},
    )

    result = search_context(session["id"], "中文检索唯一词", limit=1)
    match = result["matches"][0]
    assert result["returned"] == 1
    assert match["content_truncated"] is False
    assert len(match["content"]) <= 4_000
    assert "中文检索唯一词" in match["snippet"]


def test_search_thread_context_tool_is_bound_to_session_and_read_only(client):
    session = client.post("/sessions", json={"title": "Tool"}).json()
    save_context_message(
        session["id"],
        {"role": "user", "content": "bound-tool-needle"},
    )
    store = SessionAgentContextStore()
    tool = SearchThreadContextTool(session_id=session["id"], store=store)

    result = asyncio.run(tool.run({"query": "bound-tool-needle"}))
    payload = json.loads(result.content)
    assert tool.read_only is True
    assert result.success is True
    assert payload["ok"] is True
    assert payload["matches"][0]["sequence"] == 1
    assert "session_id" not in tool.spec()["function"]["parameters"]["properties"]

    invalid = asyncio.run(tool.run({"query": "x", "session_id": "other"}))
    assert invalid.success is False
    assert json.loads(invalid.content)["error"] == "invalid_arguments"


def test_context_tool_provider_exposes_search_in_act_and_plan_modes(client, tmp_path):
    session = client.post("/sessions", json={"title": "Provider"}).json()
    provider = ContextToolProvider(SessionAgentContextStore())
    context = ToolDiscoveryContext(
        session_id=session["id"],
        workspace=str(tmp_path),
        backend=LocalBackend(str(tmp_path)),
        mode="act",
    )
    descriptors = provider.discover(context)
    router = ToolRouter(descriptors)

    assert router.allowed_names(mode="act") == {SEARCH_THREAD_CONTEXT_NAME}
    assert router.allowed_names(mode="plan") == {SEARCH_THREAD_CONTEXT_NAME}


def test_model_loop_can_call_thread_context_and_continue(monkeypatch, client):
    session = client.post("/sessions", json={"title": "Loop"}).json()
    save_context_message(
        session["id"],
        {"role": "user", "content": "loop-history-needle"},
    )
    store = SessionAgentContextStore()
    router = ToolRouter(
        ContextToolProvider(store).discover(
            ToolDiscoveryContext(
                session_id=session["id"],
                workspace=None,
                backend=None,
                mode="act",
            )
        )
    )
    calls: list[list[dict]] = []

    async def fake_stream(messages, tools=None):
        calls.append(messages)
        if len(calls) == 1:
            yield {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_context",
                        "type": "function",
                        "function": {
                            "name": SEARCH_THREAD_CONTEXT_NAME,
                            "arguments": json.dumps(
                                {"query": "loop-history-needle"}
                            ),
                        },
                    }
                ]
            }
            return
        yield {"content": "I found the historical context."}

    from automata_api.agent import llm

    monkeypatch.setattr(llm, "stream_chat_completion", fake_stream)

    async def collect() -> list[AgentLoopEvent]:
        return [
            event
            async for event in stream_model_loop(
                messages=[{"role": "system", "content": "test"}],
                compression_config=ContextCompressionConfig(False, 100_000, 20_000),
                model="test-model",
                mode="act",
                allowed_tool_names=None,
                router=router,
                session_id=session["id"],
                store=store,
                cancellation=CancellationToken(),
            )
        ]

    events = asyncio.run(collect())
    assert [event["type"] for event in events] == [
        "agent_step",
        "tool_call",
        "tool_result",
        "agent_step",
        "token",
        "final",
    ]
    assert len(calls) == 2
    assert calls[1][-1]["role"] == "tool"
    assert "loop-history-needle" in calls[1][-1]["content"]
