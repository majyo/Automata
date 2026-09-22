"""Transactional persistence and process-control ports owned by Runs."""

from typing import Any, Literal, Protocol

from automata_api.core.runs.state import RunMode


class RunStore(Protocol):
    """Storage port for run lifecycle, events and plan attempts.

    The application layer depends on this port, not on the SQLite module.
    Each method expresses one complete business operation and owns its
    transaction, so callers never compose multi-step SQL themselves.
    """

    def interrupt_stale_runs(self, owner_instance_id: str) -> list[dict[str, Any]]: ...

    def prune_terminal_run_events(self, retention_days: int) -> int: ...

    def create_prompt_run(
        self,
        *,
        session_id: str,
        prompt: str,
        mode: RunMode,
        owner_instance_id: str,
        permission_preset: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]: ...

    def enqueue_steering_input(
        self,
        *,
        session_id: str,
        target_run_id: str,
        prompt: str,
        request_id: str,
    ) -> tuple[dict[str, Any], bool]: ...

    def enqueue_queued_input(
        self,
        *,
        session_id: str,
        prompt: str,
        mode: RunMode,
        skills: Any,
        request_id: str,
    ) -> tuple[dict[str, Any], bool]: ...

    def get_input(
        self, *, session_id: str, request_id: str
    ) -> dict[str, Any] | None: ...

    def claim_steering_inputs(self, run_id: str) -> list[dict[str, Any]]: ...

    def mark_input_applied(
        self,
        input_id: str,
        *,
        run_id: str,
        message_id: str,
    ) -> dict[str, Any]: ...

    def reject_input(
        self,
        input_id: str,
        *,
        error_code: str,
    ) -> dict[str, Any]: ...

    def cancel_input(
        self,
        *,
        session_id: str,
        input_id: str,
    ) -> dict[str, Any]: ...

    def cancel_unapplied_inputs_for_run(
        self,
        run_id: str,
        *,
        error_code: str,
    ) -> int: ...

    def cancel_queued_inputs_for_run(
        self,
        run_id: str,
        *,
        error_code: str,
    ) -> list[dict[str, Any]]: ...

    def claim_next_queued_input(
        self,
        *,
        session_id: str,
        owner_instance_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None: ...

    def pending_queue_session_ids(self) -> list[str]: ...

    def begin_plan_execution(
        self,
        *,
        session_id: str,
        plan_id: str,
        request_id: str,
        owner_instance_id: str,
        retry: bool,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]: ...

    def transition_run(
        self,
        run_id: str,
        *,
        expected: tuple[str, ...],
        target: str,
    ) -> dict[str, Any]: ...

    def append_event(
        self,
        run_id: str,
        payload: dict[str, Any],
        *,
        category: Literal["runtime", "trace"] = "runtime",
        max_payload_bytes: int = 65_536,
    ) -> dict[str, Any]: ...

    def finish_run(
        self,
        run_id: str,
        *,
        status: Literal["completed", "failed", "cancelled", "interrupted"],
        event: dict[str, Any],
        response_message_id: str | None = None,
        plan_id: str | None = None,
        response_content: str | None = None,
        plan_content: str | None = None,
        error_code: str | None = None,
        public_error: str | None = None,
    ) -> dict[str, Any]: ...

    def get_run(self, run_id: str) -> dict[str, Any]: ...

    def get_session_run(self, session_id: str, run_id: str) -> dict[str, Any]: ...

    def list_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        through_sequence: int | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]: ...

    def list_runs(
        self,
        *,
        session_id: str | None = None,
        non_terminal_only: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...

    def list_plan_attempts(
        self, session_id: str, plan_id: str
    ) -> list[dict[str, Any]]: ...


class RunProcesses(Protocol):
    async def terminate_run(self, run_id: str) -> None: ...
    async def terminate_all(self) -> None: ...
