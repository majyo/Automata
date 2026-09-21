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
