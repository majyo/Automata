import asyncio
import json
import logging
import os
import queue
import sqlite3
import time
from pathlib import Path

import pytest

from automata_api.agent import llm
from automata_api.config import AgentConfig
from automata_api.observability import (
    emit_content_record,
    emit_profile_event,
    get_observability_manager,
    observe_span,
    start_observability,
    stop_observability,
)
from automata_api.observability.config import (
    ObservabilityConfig,
    ObservabilityConfigurationError,
    get_observability_config,
)
from automata_api.observability.retention import enforce_file_retention
from automata_api.observability.runtime import ObservabilityManager
from automata_api.observability.store import ObservabilityStore


def observability_config(
    tmp_path: Path,
    *,
    mode: str = "diagnostic",
    capture_content: bool = False,
    queue_size: int = 128,
    critical_queue_size: int = 16,
) -> ObservabilityConfig:
    return ObservabilityConfig(
        mode="profile" if mode == "profile" else "diagnostic",
        capture_content=capture_content,
        output_dir=tmp_path / "observability",
        queue_size=queue_size,
        critical_queue_size=critical_queue_size,
        sample_interval_ms=50,
        file_max_bytes=1024 * 1024,
        log_retention_days=30,
        log_max_bytes=32 * 1024 * 1024,
        profile_retention_days=7,
        profile_max_bytes=32 * 1024 * 1024,
        content_profile_retention_hours=24,
        content_profile_max_bytes=16 * 1024 * 1024,
        log_level="INFO",
    )


def read_jsonl_files(directory: Path) -> list[dict]:
    records = []
    for path in sorted(directory.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            records.append(json.loads(line))
    return records


def test_observability_config_rejects_content_capture_without_profile(
    monkeypatch,
):
    monkeypatch.setenv("AUTOMATA_OBSERVABILITY_MODE", "diagnostic")
    monkeypatch.setenv("AUTOMATA_PROFILE_CAPTURE_CONTENT", "true")

    with pytest.raises(
        ObservabilityConfigurationError,
        match="requires.*profile",
    ):
        get_observability_config()


def test_diagnostic_records_spans_without_content(tmp_path):
    sentinel = "PROMPT-SENTINEL-MUST-NOT-LEAK"

    async def scenario():
        await start_observability(observability_config(tmp_path))
        async with observe_span(
            "agent.run",
            run_id="11111111-1111-1111-1111-111111111111",
            session_id="session-1",
            root=True,
            critical=True,
        ) as root:
            root.event("checkpoint", {"count": 2})
            emit_content_record("llm.request", {"prompt": sentinel})
            logging.getLogger("automata_api.test").warning(
                "Provider request failed: %s", sentinel
            )
        await stop_observability()

    asyncio.run(scenario())

    records = read_jsonl_files(
        tmp_path / "observability" / "logs"
    )
    serialized = json.dumps(records, ensure_ascii=False)
    assert sentinel not in serialized
    assert "Provider request failed: %s" in serialized
    assert any(
        record.get("record_type") == "span_end"
        and record.get("name") == "agent.run"
        for record in records
    )
    assert not (tmp_path / "observability" / "profiles").exists()

    db = sqlite3.connect(
        tmp_path / "observability" / "observability.db"
    )
    try:
        assert db.execute("SELECT COUNT(*) FROM traces").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM spans").fetchone()[0] == 1
    finally:
        db.close()


def test_profile_writes_samples_and_explicit_redacted_content(tmp_path):
    async def scenario():
        await start_observability(
            observability_config(
                tmp_path,
                mode="profile",
                capture_content=True,
            )
        )
        async with observe_span(
            "agent.run",
            run_id="22222222-2222-2222-2222-222222222222",
            root=True,
        ):
            emit_profile_event("llm.sse_chunk", {"content_chars": 3})
            emit_content_record(
                "llm.request",
                {
                    "prompt": "captured prompt",
                    "Authorization": "Bearer should-not-appear",
                },
            )
            await asyncio.sleep(0.08)
        await stop_observability()

    asyncio.run(scenario())

    profile_dirs = list(
        (tmp_path / "observability" / "profiles").iterdir()
    )
    assert len(profile_dirs) == 1
    profile_dir = profile_dirs[0]
    manifest = json.loads(
        (profile_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["clean_shutdown"] is True
    assert manifest["capture_content"] is True
    assert manifest["collector_stats"]["profile_samples"] >= 1
    assert (
        manifest["collector_stats"]["profile_sampler_overhead_ns"] > 0
    )
    assert list(profile_dir.glob("samples-*.jsonl"))
    assert list(profile_dir.glob("events-*.jsonl"))
    content = "\n".join(
        path.read_text(encoding="utf-8")
        for path in profile_dir.glob("content-*.jsonl")
    )
    assert "captured prompt" in content
    assert "should-not-appear" not in content
    assert "[redacted]" in content
    samples = read_jsonl_files(profile_dir)
    resource_sample = next(
        record
        for record in samples
        if record.get("record_type") == "resource_sample"
    )
    assert "event_loop_lag_ms" in resource_sample["attributes"]
    assert "managed_process_count" in resource_sample["attributes"]
    if os.name == "nt":
        assert resource_sample["attributes"]["rss_bytes"] > 0


def test_backpressure_drops_normal_events_and_falls_back_for_critical(
    tmp_path,
):
    manager = ObservabilityManager()
    manager.config = observability_config(
        tmp_path,
        queue_size=1,
        critical_queue_size=1,
    )
    manager.config.output_dir.mkdir(parents=True)
    manager.started = True
    manager.normal_queue = queue.Queue(maxsize=1)
    manager.critical_queue = queue.Queue(maxsize=1)

    manager.emit({"record_type": "log", "message": "first"})
    manager.emit({"record_type": "log", "message": "dropped"})
    manager.emit(
        {"record_type": "log", "message": "critical-first"},
        critical=True,
    )
    manager.emit(
        {"record_type": "log", "message": "critical-fallback"},
        critical=True,
    )

    stats = manager.stats_snapshot()
    assert stats["dropped_events"] == 1
    assert stats["critical_fallback_writes"] == 1
    fallback = (
        manager.config.output_dir / "critical-fallback.jsonl"
    ).read_text(encoding="utf-8")
    assert "critical-fallback" in fallback


def test_file_retention_removes_old_and_over_budget_files(tmp_path):
    root = tmp_path / "logs"
    root.mkdir()
    old = root / "old.jsonl"
    recent_large = root / "recent-large.jsonl"
    recent_small = root / "recent-small.jsonl"
    old.write_bytes(b"x" * 4)
    recent_large.write_bytes(b"x" * 10)
    recent_small.write_bytes(b"x" * 3)
    old_time = time.time() - 10_000
    os.utime(old, (old_time, old_time))
    now = time.time()
    os.utime(recent_large, (now - 2, now - 2))
    os.utime(recent_small, (now, now))

    enforce_file_retention(
        root,
        max_age_seconds=100,
        max_total_bytes=5,
    )

    assert not old.exists()
    assert not recent_large.exists()
    assert recent_small.exists()


def test_span_index_is_removed_when_profile_artifact_is_gone(tmp_path):
    store = ObservabilityStore(tmp_path / "observability.db")
    store.open()
    try:
        store.write_batch(
            [
                {
                    "record_type": "profile_session_start",
                    "profile_session_id": "profile-1",
                    "boot_id": "boot-1",
                    "mode": "profile",
                    "capture_content": False,
                    "pid": 123,
                    "timestamp_utc": "2026-01-01T00:00:00+00:00",
                    "artifact_path": str(tmp_path / "missing-profile"),
                },
                {
                    "record_type": "trace_start",
                    "profile_session_id": "profile-1",
                    "trace_id": "a" * 32,
                    "span_id": "b" * 16,
                    "run_id": "run-1",
                    "session_id": "session-1",
                    "timestamp_utc": "2026-01-01T00:00:00+00:00",
                },
                {
                    "record_type": "span_end",
                    "trace_id": "a" * 32,
                    "span_id": "b" * 16,
                    "parent_span_id": None,
                    "name": "agent.run",
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "timestamp_utc": "2026-01-01T00:00:01+00:00",
                    "duration_ns": 1_000_000_000,
                    "status": "ok",
                    "attributes": {},
                },
            ]
        )
        store.prune_missing_profile_artifacts()
        assert store.connection is not None
        assert (
            store.connection.execute(
                "SELECT COUNT(*) FROM profile_sessions"
            ).fetchone()[0]
            == 0
        )
        assert (
            store.connection.execute(
                "SELECT COUNT(*) FROM traces"
            ).fetchone()[0]
            == 0
        )
        assert (
            store.connection.execute(
                "SELECT COUNT(*) FROM spans"
            ).fetchone()[0]
            == 0
        )
    finally:
        store.close()


def test_stream_usage_chunk_is_preserved_without_choices():
    assert llm.parse_stream_delta(
        '{"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":3}}'
    ) == {
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 3,
        }
    }


def test_profile_captures_llm_stream_milestones_without_payload_content(
    tmp_path, monkeypatch
):
    sentinel = "LLM-PAYLOAD-SENTINEL"

    class FakeStreamResponse:
        status_code = 200
        headers = {"x-request-id": "request-123"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def aiter_lines(self):
            yield (
                'data: {"choices":[{"delta":'
                '{"reasoning_content":"think"}}]}'
            )
            yield (
                'data: {"choices":[{"delta":{"content":"answer"},'
                '"finish_reason":"stop"}],'
                '"usage":{"completion_tokens":2}}'
            )
            yield "data: [DONE]"

    class FakeClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        def stream(self, method, url, headers=None, json=None):
            return FakeStreamResponse()

    monkeypatch.setattr(
        llm,
        "get_agent_config",
        lambda: AgentConfig(
            api_key="not-logged",
            base_url="https://provider.test",
            model="profile-model",
            timeout_seconds=10,
            temperature=0,
        ),
    )
    monkeypatch.setattr(llm.httpx, "AsyncClient", FakeClient)

    async def scenario():
        await start_observability(
            observability_config(tmp_path, mode="profile")
        )
        chunks = [
            delta
            async for delta in llm.stream_chat_completion(
                [{"role": "user", "content": sentinel}]
            )
        ]
        await stop_observability()
        return chunks

    chunks = asyncio.run(scenario())
    assert chunks[-1]["content"] == "answer"
    profile_dir = next(
        (tmp_path / "observability" / "profiles").iterdir()
    )
    records = read_jsonl_files(profile_dir)
    event_names = {
        record.get("name")
        for record in records
        if record.get("record_type") in {
            "span_event",
            "profile_event",
        }
    }
    assert {
        "response_headers_received",
        "first_sse_event",
        "first_reasoning_delta",
        "first_content_delta",
        "stream_completed",
        "llm.sse_chunk",
    } <= event_names
    llm_span = next(
        record
        for record in records
        if record.get("record_type") == "span_end"
        and record.get("name") == "llm.call"
    )
    assert llm_span["attributes"]["chunk_count"] == 2
    assert llm_span["attributes"]["usage"] == {
        "completion_tokens": 2
    }
    serialized = json.dumps(records, ensure_ascii=False)
    assert sentinel not in serialized
    assert "not-logged" not in serialized


def test_observability_startup_io_failure_is_fail_open(
    tmp_path, monkeypatch
):
    manager = ObservabilityManager()
    config = observability_config(tmp_path)
    original_mkdir = Path.mkdir

    def failing_mkdir(path, *args, **kwargs):
        if path == config.output_dir:
            raise OSError("read only")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", failing_mkdir)
    asyncio.run(manager.start(config))

    assert manager.started is False
    assert manager.writer is None


@pytest.fixture(autouse=True)
def stop_global_observer_after_test():
    yield
    manager = get_observability_manager()
    if manager.started:
        asyncio.run(stop_observability())
