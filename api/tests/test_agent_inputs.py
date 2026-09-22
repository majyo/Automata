import pytest

from automata_api.core.runs import state
from automata_api.infrastructure.persistence import runs
from automata_api.infrastructure.persistence.sessions import save_message


def test_steering_inputs_are_fifo_idempotent_and_claimed_at_safe_boundary(client):
    session = client.post("/sessions", json={"title": "Steering"}).json()
    run, _ = runs.create_prompt_run(
        session_id=session["id"],
        prompt="start",
        mode="act",
        owner_instance_id="instance-a",
    )
    runs.transition_run(run["id"], expected=("queued",), target="running")

    first, duplicate = runs.enqueue_steering_input(
        session_id=session["id"],
        target_run_id=run["id"],
        prompt="focus on tests",
        request_id="steer-1",
    )
    assert duplicate is False
    assert first["status"] == "pending"

    same, duplicate = runs.enqueue_steering_input(
        session_id=session["id"],
        target_run_id=run["id"],
        prompt="focus on tests",
        request_id="steer-1",
    )
    assert duplicate is True
    assert same["id"] == first["id"]

    claimed = runs.claim_steering_inputs(run["id"])
    assert [item["id"] for item in claimed] == [first["id"]]
    assert claimed[0]["status"] == "applying"

    message = save_message(session["id"], "user", "focus on tests")
    applied = runs.mark_input_applied(
        first["id"], run_id=run["id"], message_id=message["id"]
    )
    assert applied["status"] == "applied"
    assert applied["message_id"] == message["id"]

    with pytest.raises(state.InputConflictError):
        runs.enqueue_steering_input(
            session_id=session["id"],
            target_run_id=run["id"],
            prompt="different prompt",
            request_id="steer-1",
        )


def test_queued_inputs_materialize_one_run_per_item_in_fifo_order(client):
    session = client.post("/sessions", json={"title": "Queue"}).json()
    first, _ = runs.create_prompt_run(
        session_id=session["id"],
        prompt="first",
        mode="act",
        owner_instance_id="instance-a",
    )
    runs.transition_run(first["id"], expected=("queued",), target="running")

    queued_one, duplicate = runs.enqueue_queued_input(
        session_id=session["id"],
        prompt="second",
        mode="act",
        skills=[{"name": "tests"}],
        request_id="queue-1",
    )
    assert duplicate is False
    queued_two, duplicate = runs.enqueue_queued_input(
        session_id=session["id"],
        prompt="third",
        mode="plan",
        skills=None,
        request_id="queue-2",
    )
    assert duplicate is False
    assert queued_one["position"] < queued_two["position"]
    assert queued_one["predecessor_run_id"] == first["id"]

    runs.finish_run(first["id"], status="completed", event={"type": "done"})
    materialized = runs.claim_next_queued_input(
        session_id=session["id"], owner_instance_id="instance-a"
    )
    assert materialized is not None
    second_run, second_input, second_message = materialized
    assert second_input["prompt"] == "second"
    assert second_input["status"] == "applied"
    assert second_input["skills"] == [{"name": "tests"}]
    assert second_message["content"] == "second"

    assert (
        runs.claim_next_queued_input(
            session_id=session["id"], owner_instance_id="instance-a"
        )
        is None
    )

    runs.transition_run(second_run["id"], expected=("queued",), target="running")
    runs.finish_run(second_run["id"], status="completed", event={"type": "done"})
    materialized = runs.claim_next_queued_input(
        session_id=session["id"], owner_instance_id="instance-a"
    )
    assert materialized is not None
    third_run, third_input, _ = materialized
    assert third_input["prompt"] == "third"
    assert third_input["mode"] == "plan"
    assert third_input["predecessor_run_id"] == second_run["id"]
    assert third_run["kind"] == "chat_plan"


def test_queue_stays_paused_after_a_successor_run_fails(client):
    session = client.post("/sessions", json={"title": "Queue pause"}).json()
    first, _ = runs.create_prompt_run(
        session_id=session["id"],
        prompt="first",
        mode="act",
        owner_instance_id="instance-a",
    )
    runs.transition_run(first["id"], expected=("queued",), target="running")
    runs.enqueue_queued_input(
        session_id=session["id"],
        prompt="second",
        mode="act",
        skills=None,
        request_id="pause-1",
    )
    runs.enqueue_queued_input(
        session_id=session["id"],
        prompt="third",
        mode="act",
        skills=None,
        request_id="pause-2",
    )
    runs.finish_run(first["id"], status="completed", event={"type": "done"})
    materialized = runs.claim_next_queued_input(
        session_id=session["id"], owner_instance_id="instance-a"
    )
    assert materialized is not None
    second_run, _, _ = materialized
    runs.transition_run(second_run["id"], expected=("queued",), target="running")
    runs.finish_run(second_run["id"], status="failed", event={"type": "error"})

    assert (
        runs.claim_next_queued_input(
            session_id=session["id"], owner_instance_id="instance-a"
        )
        is None
    )


def test_pending_inputs_can_be_withdrawn_before_delivery(client):
    session = client.post("/sessions", json={"title": "Withdraw"}).json()
    run, _ = runs.create_prompt_run(
        session_id=session["id"],
        prompt="start",
        mode="act",
        owner_instance_id="instance-a",
    )
    runs.transition_run(run["id"], expected=("queued",), target="running")
    steering, _ = runs.enqueue_steering_input(
        session_id=session["id"],
        target_run_id=run["id"],
        prompt="focus on tests",
        request_id="withdraw-steer",
    )
    queued, _ = runs.enqueue_queued_input(
        session_id=session["id"],
        prompt="second",
        mode="act",
        skills=None,
        request_id="withdraw-queue",
    )

    cancelled = runs.cancel_input(session_id=session["id"], input_id=steering["id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["error_code"] == "cancelled_by_user"

    # Withdrawing twice is idempotent: a client may retry after a lost ack.
    again = runs.cancel_input(session_id=session["id"], input_id=steering["id"])
    assert again["status"] == "cancelled"

    assert runs.claim_steering_inputs(run["id"]) == []

    runs.finish_run(run["id"], status="completed", event={"type": "done"})
    materialized = runs.claim_next_queued_input(
        session_id=session["id"], owner_instance_id="instance-a"
    )
    assert materialized is not None
    _, second_input, _ = materialized
    assert second_input["id"] == queued["id"]


def test_claimed_and_foreign_inputs_cannot_be_withdrawn(client):
    other = client.post("/sessions", json={"title": "Other"}).json()
    session = client.post("/sessions", json={"title": "Claimed"}).json()
    run, _ = runs.create_prompt_run(
        session_id=session["id"],
        prompt="start",
        mode="act",
        owner_instance_id="instance-a",
    )
    runs.transition_run(run["id"], expected=("queued",), target="running")
    steering, _ = runs.enqueue_steering_input(
        session_id=session["id"],
        target_run_id=run["id"],
        prompt="focus on tests",
        request_id="claimed-steer",
    )
    runs.claim_steering_inputs(run["id"])

    with pytest.raises(state.RunStateError):
        runs.cancel_input(session_id=session["id"], input_id=steering["id"])

    with pytest.raises(state.RunNotFoundError):
        runs.cancel_input(session_id=other["id"], input_id=steering["id"])


def test_a_failed_run_withdraws_the_follow_ups_queued_behind_it(client):
    session = client.post("/sessions", json={"title": "Failed"}).json()
    failed, _ = runs.create_prompt_run(
        session_id=session["id"],
        prompt="first",
        mode="act",
        owner_instance_id="instance-a",
    )
    runs.transition_run(failed["id"], expected=("queued",), target="running")
    first, _ = runs.enqueue_queued_input(
        session_id=session["id"],
        prompt="second",
        mode="act",
        skills=None,
        request_id="failed-queue-1",
    )
    second, _ = runs.enqueue_queued_input(
        session_id=session["id"],
        prompt="third",
        mode="act",
        skills=None,
        request_id="failed-queue-2",
    )
    runs.finish_run(failed["id"], status="failed", event={"type": "error"})

    cancelled = runs.cancel_queued_inputs_for_run(
        failed["id"], error_code="predecessor_failed"
    )

    assert [row["id"] for row in cancelled] == [first["id"], second["id"]]
    assert {row["status"] for row in cancelled} == {"cancelled"}
    assert {row["error_code"] for row in cancelled} == {"predecessor_failed"}

    # Nothing is left to run, so the session's queue is not blocked for good.
    assert (
        runs.claim_next_queued_input(
            session_id=session["id"], owner_instance_id="instance-a"
        )
        is None
    )
    assert runs.pending_queue_session_ids() == []

    # Withdrawing twice is harmless: the run finalizer may run again.
    assert (
        runs.cancel_queued_inputs_for_run(failed["id"], error_code="predecessor_failed")
        == cancelled
    )


def test_a_completed_run_keeps_its_follow_ups(client):
    session = client.post("/sessions", json={"title": "Completed"}).json()
    completed, _ = runs.create_prompt_run(
        session_id=session["id"],
        prompt="first",
        mode="act",
        owner_instance_id="instance-a",
    )
    runs.transition_run(completed["id"], expected=("queued",), target="running")
    queued, _ = runs.enqueue_queued_input(
        session_id=session["id"],
        prompt="second",
        mode="act",
        skills=None,
        request_id="completed-queue-1",
    )
    runs.finish_run(completed["id"], status="completed", event={"type": "done"})

    # Nothing withdraws the follow-ups of a Run that completed: they resume.
    materialized = runs.claim_next_queued_input(
        session_id=session["id"], owner_instance_id="instance-a"
    )
    assert materialized is not None
    _, second_input, _ = materialized
    assert second_input["id"] == queued["id"]
