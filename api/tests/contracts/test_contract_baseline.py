"""M0 contract baseline checks.

These tests pin the externally visible protocol: persisted Run events,
connection control messages, error codes and ToolResult payload shape.
They are deliberately about *shape and vocabulary*, not about business
flow, so they survive the structure refactoring while still failing if a
published field, default or code changes.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

CONTRACTS_DIR = Path(__file__).resolve().parent
RUN_EVENTS = json.loads((CONTRACTS_DIR / "run_events.json").read_text("utf-8"))
CONTROL = json.loads((CONTRACTS_DIR / "control_messages.json").read_text("utf-8"))
ERRORS = json.loads((CONTRACTS_DIR / "error_codes.json").read_text("utf-8"))
TOOL_RESULTS = json.loads((CONTRACTS_DIR / "tool_results.json").read_text("utf-8"))
REPLAY = json.loads((CONTRACTS_DIR / "replay_scenarios.json").read_text("utf-8"))


def test_every_persisted_event_carries_the_versioned_envelope():
    required = set(RUN_EVENTS["envelope_required_fields"])

    for event in RUN_EVENTS["events"]:
        missing = required - set(event)
        assert missing == set(), f"{event.get('type')} is missing {sorted(missing)}"
        assert event["schema_version"] == RUN_EVENTS["schema_version"]
        assert isinstance(event["seq"], int) and event["seq"] > 0


def test_persisted_sequences_are_strictly_increasing():
    sequences = [event["seq"] for event in RUN_EVENTS["events"]]

    assert sequences == sorted(sequences)
    assert len(set(sequences)) == len(sequences)


def test_terminal_event_types_match_the_published_mapping():
    terminal_types = {
        event["type"]
        for event in RUN_EVENTS["events"]
        if event["type"] in set(RUN_EVENTS["terminal_events"].values())
    }

    assert terminal_types == set(RUN_EVENTS["terminal_events"].values())


def test_control_messages_are_not_persisted_run_events():
    persisted_types = {event["type"] for event in RUN_EVENTS["events"]}
    control_types = {message["type"] for message in CONTROL["messages"]}

    assert CONTROL["not_persisted"] is True
    assert persisted_types & control_types == set()
    for message in CONTROL["messages"]:
        assert "seq" not in message, f"{message['type']} must not be sequenced"


def test_client_commands_only_use_published_fields():
    allowed = {
        "type",
        "session_id",
        "run_id",
        "plan_id",
        "prompt",
        "mode",
        "skills",
        "request_id",
        "approval_id",
        "decision",
        "after_sequence",
        "confirm_possible_duplicate_side_effects",
    }

    for command in CONTROL["client_commands"]:
        assert set(command) <= allowed, f"unexpected field in {command}"


def test_sandbox_error_codes_match_the_runtime_vocabulary():
    from automata_api.agent.execution.sandbox.protocol import (
        _explicit_failure,
    )

    for code in ERRORS["sandbox_error_codes"]:
        failure = _explicit_failure(
            "AUTOMATA_SANDBOX_ERROR:" + json.dumps({"code": code, "message": "m"})
        )
        assert failure is not None
        assert failure.code == code, f"{code} was not preserved by the classifier"


def test_unknown_sandbox_codes_collapse_to_protocol_error():
    from automata_api.agent.execution.sandbox.protocol import _explicit_failure

    failure = _explicit_failure(
        "AUTOMATA_SANDBOX_ERROR:" + json.dumps({"code": "not_a_real_code"})
    )

    assert failure is not None
    assert failure.code == "sandbox_protocol_error"


def test_public_run_error_codes_are_reachable_from_the_error_wrapper():
    from automata_api.agent.execution.model import PublicRunError

    for code in ERRORS["public_run_errors"]:
        error = PublicRunError(code, "message")
        assert error.code == code
        assert error.public_message == "message"


def test_tool_result_contract_matches_the_dataclass():
    from automata_api.agent.tools._core import ToolResult

    fields = [field.name for field in dataclasses.fields(ToolResult)]
    assert fields == TOOL_RESULTS["result_fields"]

    for sample in TOOL_RESULTS["samples"]:
        result = ToolResult(
            name=sample["name"],
            arguments=sample["arguments"],
            content=json.dumps({key: None for key in sample["content_keys"]}),
            success=sample["success"],
            error_code=sample["error_code"],
            sandbox=sample["sandbox"],
        )
        assert result.name == sample["name"]
        assert result.success is sample["success"]
        assert result.error_code == sample["error_code"]
        for marker in TOOL_RESULTS["content_markers"]:
            assert marker in sample["content_keys"], (
                f"{sample['name']} payload must carry the {marker!r} marker"
            )


def test_process_session_error_codes_are_stable():
    from automata_api.agent.execution.process_sessions import ProcessSessionError

    for code in ERRORS["process_session_error_codes"]:
        assert ProcessSessionError(code, "message").code == code


@pytest.mark.parametrize(
    "scenario", REPLAY["scenarios"], ids=[s["id"] for s in REPLAY["scenarios"]]
)
def test_replay_scenarios_are_self_consistent(scenario):
    expect = scenario["expect"]
    persisted = scenario["persisted_sequences"]
    after = scenario["after_sequence"]

    if expect["cursor_error"]:
        retained_from = scenario.get("retained_from_sequence", 1)
        out_of_range = after < 0 or after > max(persisted)
        trimmed = after < retained_from - 1
        assert out_of_range or trimmed, (
            "a cursor error must come from an out-of-range cursor or from "
            "history that has already been trimmed"
        )
        assert expect["replayed_sequences"] == []
        return

    assert 0 <= after <= max(persisted)
    expected_replay = [seq for seq in persisted if seq > after]
    assert expect["replayed_sequences"] == sorted(expected_replay)
    assert expect["last_sequence"] >= max(persisted)


def test_replay_live_events_are_deduplicated_against_the_watermark():
    """Buffered live events at or below the watermark must not be resent."""
    for scenario in REPLAY["scenarios"]:
        watermark = max(scenario["persisted_sequences"])
        for live in scenario["live_during_replay"]:
            already_replayed = live["seq"] in scenario["expect"][
                "replayed_sequences"
            ]
            delivered = live["seq"] in scenario["expect"][
                "delivered_after_watermark"
            ]
            if live["seq"] <= watermark:
                assert not delivered, (
                    f"{scenario['id']}: seq {live['seq']} is at or below the "
                    "watermark and must be dropped as a duplicate"
                )
            assert not (already_replayed and delivered), (
                f"{scenario['id']}: seq {live['seq']} delivered twice"
            )
