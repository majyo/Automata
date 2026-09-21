"""Decode inbound WebSocket frames into commands.

The wire format is a loose JSON object; this module is the single place
that turns it into typed values and decides what a malformed frame means.
It performs no I/O, so the command vocabulary can be tested directly
instead of through a socket.

Payload field names are part of the published protocol and must not change.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

PROMPT_TYPES = frozenset({"prompt", "approve_plan", "retry_plan"})
ALL_COMMAND_TYPES = PROMPT_TYPES | {
    "tool_approval_response",
    "cancel_run",
    "resume_run",
}

AgentMode = Literal["act", "plan"]
InputDelivery = Literal["new", "steer", "queue"]


@dataclass(frozen=True)
class PromptCommand:
    """Deliver a prompt as a new Run, steering input, or queued follow-up."""

    session_id: str
    prompt: str
    mode: AgentMode
    skills: Any = None
    delivery: InputDelivery = "new"
    run_id: str = ""
    request_id: str = ""


@dataclass(frozen=True)
class PlanExecutionCommand:
    """Approve a pending plan, or explicitly retry it."""

    session_id: str
    plan_id: str
    request_id: str
    retry: bool
    # Retry requires the client to acknowledge that side effects may repeat.
    confirmed_duplicate_side_effects: bool


@dataclass(frozen=True)
class ApprovalResponseCommand:
    """Resolve a pending tool approval."""

    run_id: str
    session_id: str
    approval_id: str
    decision: str


@dataclass(frozen=True)
class CancelRunCommand:
    run_id: str
    session_id: str


@dataclass(frozen=True)
class ResumeRunCommand:
    """Replay a Run's events after a cursor, then go live."""

    run_id: str
    session_id: str
    after_sequence: int


@dataclass(frozen=True)
class InvalidCommand:
    """The frame cannot be acted on; ``message`` is what the client sees."""

    message: str
    code: str | None = None


Command = (
    PromptCommand
    | PlanExecutionCommand
    | ApprovalResponseCommand
    | CancelRunCommand
    | ResumeRunCommand
    | InvalidCommand
)

UNSUPPORTED_MESSAGE = "Unsupported message type"
MISSING_SESSION_ID = "Missing session_id"
MISSING_PROMPT = "Missing prompt"
MISSING_RUN_ID = "Missing run_id for steering input"
INVALID_DELIVERY = "Invalid prompt delivery"
MISSING_PLAN_ID = "Missing plan_id"
DUPLICATE_SIDE_EFFECT_CODE = "duplicate_side_effect_confirmation_required"
DUPLICATE_SIDE_EFFECT_MESSAGE = (
    "Retry requires confirmation of possible duplicate side effects."
)


def decode_command(payload: Mapping[str, Any]) -> Command:
    """Turn one inbound frame into a command or an invalid-command marker."""
    payload_type = payload.get("type")

    if payload_type == "tool_approval_response":
        return ApprovalResponseCommand(
            run_id=_text(payload, "run_id"),
            session_id=_text(payload, "session_id"),
            approval_id=_text(payload, "approval_id"),
            decision=_text(payload, "decision"),
        )
    if payload_type == "cancel_run":
        return CancelRunCommand(
            run_id=_text(payload, "run_id"),
            session_id=_text(payload, "session_id"),
        )
    if payload_type == "resume_run":
        return ResumeRunCommand(
            run_id=_text(payload, "run_id"),
            session_id=_text(payload, "session_id"),
            after_sequence=_integer(payload.get("after_sequence", 0), default=-1),
        )

    if payload_type not in PROMPT_TYPES:
        return InvalidCommand(UNSUPPORTED_MESSAGE)

    session_id = _text(payload, "session_id")
    if not session_id:
        return InvalidCommand(MISSING_SESSION_ID)

    if payload_type == "prompt":
        prompt = _text(payload, "prompt")
        if not prompt:
            return InvalidCommand(MISSING_PROMPT)
        delivery_value = _text(payload, "delivery") or "new"
        if delivery_value not in {"new", "steer", "queue"}:
            return InvalidCommand(INVALID_DELIVERY)
        run_id = _text(payload, "run_id")
        if delivery_value == "steer" and not run_id:
            return InvalidCommand(MISSING_RUN_ID)
        return PromptCommand(
            session_id=session_id,
            prompt=prompt,
            mode="plan" if _text(payload, "mode") == "plan" else "act",
            skills=payload.get("skills"),
            delivery=delivery_value,  # type: ignore[arg-type]
            run_id=run_id,
            request_id=_text(payload, "request_id") or uuid.uuid4().hex,
        )

    plan_id = _text(payload, "plan_id")
    if not plan_id:
        return InvalidCommand(MISSING_PLAN_ID)
    retry = payload_type == "retry_plan"
    if retry and payload.get("confirm_possible_duplicate_side_effects") is not True:
        return InvalidCommand(
            DUPLICATE_SIDE_EFFECT_MESSAGE,
            code=DUPLICATE_SIDE_EFFECT_CODE,
        )
    return PlanExecutionCommand(
        session_id=session_id,
        plan_id=plan_id,
        request_id=_text(payload, "request_id") or uuid.uuid4().hex,
        retry=retry,
        confirmed_duplicate_side_effects=(
            payload.get("confirm_possible_duplicate_side_effects") is True
        ),
    )


def _text(payload: Mapping[str, Any], key: str) -> str:
    """Read a trimmed string field, treating blanks and non-strings as empty."""
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""


def _integer(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
