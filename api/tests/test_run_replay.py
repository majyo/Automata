"""Run event replay: the M0 replay scenarios, executed for real.

These tests drive :class:`ReplayService` against a real temporary SQLite
database, so the cursor rules, pagination and the replay/live handover are
verified against the same JSON scenarios the frontend fixtures describe.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from automata_api.repositories import runs as run_repository
from automata_api.runs.replay import ReplayService

REPLAY_CONTRACT = json.loads(
    (
        Path(__file__).resolve().parent / "contracts" / "replay_scenarios.json"
    ).read_text("utf-8")
)
SCENARIOS = REPLAY_CONTRACT["scenarios"]


class RecordingSender:
    """A sender that records the protocol messages replay produces."""

    def __init__(self, *, drop_through_watermark: bool = True) -> None:
        self.sent: list[dict[str, Any]] = []
        self.replayed: list[int] = []
        self.delivered: list[int] = []
        self.buffers: dict[str, int] = {}
        self.aborted: list[str] = []
        self.completion: dict[str, Any] | None = None
        self._drop_through = drop_through_watermark
        self.live: dict[str, list[dict[str, Any]]] = {}

    async def send_json(self, data: Any) -> None:
        self.sent.append(data)

    async def begin_replay(self, run_id: str) -> None:
        self.buffers[run_id] = 0

    async def send_replay_event(self, event: dict[str, Any]) -> None:
        self.replayed.append(int(event["seq"]))

    async def abort_replay(self, run_id: str) -> None:
        self.aborted.append(run_id)
        self.buffers.pop(run_id, None)

    async def publish_json(self, data: Any) -> None:
        """Live traffic while replay is in flight (mirrors the real sender)."""
        run_id = data.get("run_id")
        seq = int(data.get("seq", 0))
        if (
            self._drop_through
            and isinstance(run_id, str)
            and run_id in self.buffers
        ):
            self.live.setdefault(run_id, []).append(data)
            return
        self.delivered.append(seq)

    async def finish_replay(
        self, run_id: str, watermark: int, completion: dict[str, Any]
    ) -> None:
        for event in sorted(
            self.live.pop(run_id, []), key=lambda item: int(item["seq"])
        ):
            if int(event["seq"]) > watermark:
                self.delivered.append(int(event["seq"]))
        self.completion = completion
        self.buffers.pop(run_id, None)


@pytest.fixture()
def database(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOMATA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "AUTOMATA_API_TOKEN", "test-api-token-that-is-at-least-32-characters"
    )
    from automata_api.db.schema import init_db

    init_db()
    return tmp_path


def make_run(session_id: str, *, prompt: str = "hello") -> str:
    run, _ = run_repository.create_prompt_run(
        session_id=session_id,
        prompt=prompt,
        mode="act",
        owner_instance_id="test-instance",
    )
    return str(run["id"])


def prune_events_before(run_id: str, sequence: int) -> None:
    """Drop retained events below ``sequence`` to simulate retention."""
    from automata_api.db.connection import connect_db, db_lock

    with db_lock, connect_db() as db:
        db.execute(
            "DELETE FROM agent_run_events WHERE run_id = ? AND sequence < ?",
            (run_id, sequence),
        )
        db.commit()


def populate(run_id: str, scenario: dict[str, Any]) -> None:
    """Create the persisted backlog the scenario describes.

    Events are appended from 1 so sequences match the scenario, then the
    early ones are deleted when the scenario models a trimmed history.
    """
    highest = max(scenario["persisted_sequences"])
    for seq in range(1, highest + 1):
        append(run_id, {"type": "token", "content": f"n{seq}"})
    retained_from = scenario.get("retained_from_sequence")
    if retained_from is not None:
        prune_events_before(run_id, int(retained_from))


def make_session() -> str:
    from automata_api.repositories.sessions import create_session

    return str(create_session("replay", None, None, "default")["id"])


def append(run_id: str, payload: dict[str, Any]) -> int:
    return int(run_repository.append_event(run_id, payload)["seq"])


def test_replay_scenarios_match_the_contract(database):
    """Every JSON scenario behaves the way the shared contract says."""
    service = ReplayService()
    for scenario in SCENARIOS:
        session_id = make_session()
        run_id = make_run(session_id)
        populate(run_id, scenario)

        sender = RecordingSender()
        outcome = asyncio.run(
            service.resume(
                sender=sender,
                session_id=session_id,
                run_id=run_id,
                after_sequence=scenario["after_sequence"],
            )
        )
        expect = scenario["expect"]

        assert outcome.cursor_error is expect["cursor_error"], scenario["id"]
        if expect["cursor_error"]:
            assert sender.replayed == [], scenario["id"]
            continue

        assert sender.replayed == expect["replayed_sequences"], scenario["id"]
        resumed = sender.sent[0]
        assert resumed["type"] == "run_resume_started"
        assert resumed["after_sequence"] == scenario["after_sequence"]
        assert sender.completion is not None
        assert sender.completion["type"] == "run_resume_complete"


def test_resume_started_precedes_replayed_events(database):
    session_id = make_session()
    run_id = make_run(session_id)
    append(run_id, {"type": "token", "content": "a"})
    append(run_id, {"type": "token", "content": "b"})

    sender = RecordingSender()
    asyncio.run(
        ReplayService().resume(
            sender=sender,
            session_id=session_id,
            run_id=run_id,
            after_sequence=0,
        )
    )

    assert sender.sent[0]["type"] == "run_resume_started"
    assert sender.sent[0]["through_sequence"] == 2


def test_unknown_run_reports_cursor_error_not_a_crash(database):
    session_id = make_session()
    make_run(session_id)

    sender = RecordingSender()
    outcome = asyncio.run(
        ReplayService().resume(
            sender=sender,
            session_id=session_id,
            run_id="does-not-exist",
            after_sequence=0,
        )
    )

    assert outcome.ok is False
    assert outcome.run_missing is True
    assert outcome.cursor_error is False


def test_live_event_at_or_below_watermark_is_dropped(database):
    """The handover must not deliver a buffered duplicate."""
    session_id = make_session()
    run_id = make_run(session_id)
    append(run_id, {"type": "token", "content": "a"})
    append(run_id, {"type": "token", "content": "b"})

    sender = RecordingSender()

    async def scenario():
        await sender.begin_replay(run_id)
        # A live event that replay is about to deliver must be suppressed.
        await sender.publish_json({"type": "token", "seq": 2, "run_id": run_id})
        await sender.finish_replay(run_id, 2, {"type": "run_resume_complete"})

    asyncio.run(scenario())

    assert 2 not in sender.delivered


def test_live_event_above_watermark_is_delivered(database):
    session_id = make_session()
    run_id = make_run(session_id)
    append(run_id, {"type": "token", "content": "a"})

    sender = RecordingSender()

    async def scenario():
        await sender.begin_replay(run_id)
        await sender.publish_json({"type": "token", "seq": 2, "run_id": run_id})
        await sender.finish_replay(run_id, 1, {"type": "run_resume_complete"})

    asyncio.run(scenario())

    assert sender.delivered == [2]


def test_replay_delivers_a_long_backlog_in_order(database):
    session_id = make_session()
    run_id = make_run(session_id)
    for index in range(25):
        append(run_id, {"type": "token", "content": f"chunk-{index}"})

    sender = RecordingSender()
    asyncio.run(
        ReplayService().resume(
            sender=sender,
            session_id=session_id,
            run_id=run_id,
            after_sequence=5,
        )
    )

    assert sender.replayed == list(range(6, 26))
    assert sender.replayed == sorted(sender.replayed)
