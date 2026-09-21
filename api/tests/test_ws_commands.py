"""M5: the WebSocket command decoder.

The decoder is pure, so the wire contract can be tested directly instead of
through a socket. These tests pin the published field names and the exact
envelopes the client receives for a malformed frame.
"""

from __future__ import annotations

import pytest

from automata_api.transport.websocket.commands import (
    DUPLICATE_SIDE_EFFECT_CODE,
    ApprovalResponseCommand,
    CancelRunCommand,
    InvalidCommand,
    PlanExecutionCommand,
    PromptCommand,
    ResumeRunCommand,
    decode_command,
)


def test_prompt_defaults_to_act_mode():
    command = decode_command({"type": "prompt", "session_id": "s1", "prompt": "do it"})

    assert isinstance(command, PromptCommand)
    assert command.session_id == "s1"
    assert command.prompt == "do it"
    assert command.mode == "act"


def test_prompt_honours_plan_mode_and_skills():
    command = decode_command(
        {
            "type": "prompt",
            "session_id": "s1",
            "prompt": "plan it",
            "mode": "plan",
            "skills": ["refactor"],
        }
    )

    assert isinstance(command, PromptCommand)
    assert command.mode == "plan"
    assert command.skills == ["refactor"]


def test_prompt_delivery_carries_run_and_idempotency_fields():
    command = decode_command(
        {
            "type": "prompt",
            "session_id": "s1",
            "prompt": "focus",
            "delivery": "steer",
            "run_id": "run-1",
            "request_id": "input-1",
        }
    )

    assert isinstance(command, PromptCommand)
    assert command.delivery == "steer"
    assert command.run_id == "run-1"
    assert command.request_id == "input-1"


def test_steering_requires_a_target_run():
    command = decode_command(
        {
            "type": "prompt",
            "session_id": "s1",
            "prompt": "focus",
            "delivery": "steer",
        }
    )

    assert isinstance(command, InvalidCommand)
    assert command.message == "Missing run_id for steering input"


def test_unknown_mode_falls_back_to_act():
    command = decode_command(
        {"type": "prompt", "session_id": "s1", "prompt": "x", "mode": "weird"}
    )

    assert isinstance(command, PromptCommand)
    assert command.mode == "act"


def test_blank_fields_are_treated_as_missing():
    """Whitespace-only values must not create a Run with an empty prompt."""
    blank_prompt = decode_command(
        {"type": "prompt", "session_id": "s1", "prompt": "   "}
    )
    assert isinstance(blank_prompt, InvalidCommand)
    assert blank_prompt.message == "Missing prompt"

    blank_session = decode_command({"type": "prompt", "session_id": "  "})
    assert isinstance(blank_session, InvalidCommand)
    assert blank_session.message == "Missing session_id"


def test_unsupported_type_is_reported():
    command = decode_command({"type": "nonsense"})

    assert isinstance(command, InvalidCommand)
    assert command.message == "Unsupported message type"
    assert command.code is None


def test_missing_type_is_reported():
    command = decode_command({})

    assert isinstance(command, InvalidCommand)
    assert command.message == "Unsupported message type"


def test_approve_plan_generates_a_request_id_when_absent():
    command = decode_command(
        {"type": "approve_plan", "session_id": "s1", "plan_id": "p1"}
    )

    assert isinstance(command, PlanExecutionCommand)
    assert command.plan_id == "p1"
    assert command.retry is False
    assert command.request_id  # a generated id, never empty


def test_approve_plan_keeps_a_supplied_request_id():
    command = decode_command(
        {
            "type": "approve_plan",
            "session_id": "s1",
            "plan_id": "p1",
            "request_id": "req-1",
        }
    )

    assert isinstance(command, PlanExecutionCommand)
    assert command.request_id == "req-1"


def test_retry_requires_duplicate_side_effect_confirmation():
    unconfirmed = decode_command(
        {"type": "retry_plan", "session_id": "s1", "plan_id": "p1"}
    )

    assert isinstance(unconfirmed, InvalidCommand)
    assert unconfirmed.code == DUPLICATE_SIDE_EFFECT_CODE

    confirmed = decode_command(
        {
            "type": "retry_plan",
            "session_id": "s1",
            "plan_id": "p1",
            "confirm_possible_duplicate_side_effects": True,
        }
    )
    assert isinstance(confirmed, PlanExecutionCommand)
    assert confirmed.retry is True
    assert confirmed.confirmed_duplicate_side_effects is True


def test_plan_command_requires_a_plan_id():
    command = decode_command({"type": "approve_plan", "session_id": "s1"})

    assert isinstance(command, InvalidCommand)
    assert command.message == "Missing plan_id"


def test_approval_response_carries_the_decision():
    command = decode_command(
        {
            "type": "tool_approval_response",
            "run_id": "r1",
            "approval_id": "a1",
            "decision": "allow_once",
        }
    )

    assert isinstance(command, ApprovalResponseCommand)
    assert command.run_id == "r1"
    assert command.approval_id == "a1"
    assert command.decision == "allow_once"


def test_cancel_run_carries_the_session_guard():
    command = decode_command({"type": "cancel_run", "run_id": "r1", "session_id": "s1"})

    assert isinstance(command, CancelRunCommand)
    assert command.run_id == "r1"
    assert command.session_id == "s1"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0, 0),
        (12, 12),
        ("7", 7),
        (None, -1),
        ("abc", -1),
        (True, 1),
    ],
)
def test_resume_run_coerces_the_cursor(raw, expected):
    command = decode_command(
        {
            "type": "resume_run",
            "run_id": "r1",
            "session_id": "s1",
            "after_sequence": raw,
        }
    )

    assert isinstance(command, ResumeRunCommand)
    assert command.after_sequence == expected
