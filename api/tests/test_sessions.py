from automata_api.repositories.sessions import (
    PlanNotFoundError,
    SessionNotFoundError,
    approve_plan,
    create_plan,
    fetch_context_summary,
    fetch_plan,
    list_messages,
    mark_plan_executed,
    save_message,
    upsert_context_summary,
)


def test_session_crud_and_messages(client):
    assert client.get("/sessions").json() == []

    created = client.post("/sessions", json={"title": "Alpha"}).json()
    assert created["title"] == "Alpha"
    assert created["message_count"] == 0

    sessions = client.get("/sessions").json()
    assert len(sessions) == 1
    assert sessions[0]["id"] == created["id"]
    assert sessions[0]["title"] == "Alpha"
    assert sessions[0]["message_count"] == 0

    updated = client.patch(
        f"/sessions/{created['id']}",
        json={"title": "Beta"},
    ).json()
    assert updated["id"] == created["id"]
    assert updated["title"] == "Beta"

    messages = client.get(f"/sessions/{created['id']}/messages")
    assert messages.status_code == 200
    assert messages.json() == []

    deleted = client.delete(f"/sessions/{created['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/sessions/{created['id']}/messages").status_code == 404


def test_context_summary_is_hidden_from_visible_messages(client):
    session = client.post("/sessions", json={"title": "Compression"}).json()
    message = save_message(
        session_id=session["id"],
        role="user",
        content="visible message",
    )

    stored = upsert_context_summary(
        session_id=session["id"],
        content="hidden summary",
        through_sequence=message["sequence"],
    )
    updated = upsert_context_summary(
        session_id=session["id"],
        content="updated hidden summary",
        through_sequence=message["sequence"] + 1,
    )

    assert stored["created_at"] == updated["created_at"]
    assert fetch_context_summary(session["id"])["content"] == "updated hidden summary"
    assert fetch_context_summary(session["id"])["through_sequence"] == message["sequence"] + 1
    assert [row["content"] for row in list_messages(session["id"])] == [
        "visible message"
    ]
    assert client.get(f"/sessions/{session['id']}/messages").json()[0]["content"] == (
        "visible message"
    )


def test_session_plans_supersede_pending_plans(client):
    session = client.post("/sessions", json={"title": "Plans"}).json()
    first_prompt = save_message(
        session_id=session["id"],
        role="user",
        content="first prompt",
    )
    first_plan_message = save_message(
        session_id=session["id"],
        role="agent",
        content="first plan",
    )
    first_plan = create_plan(
        session_id=session["id"],
        prompt_message_id=first_prompt["id"],
        plan_message_id=first_plan_message["id"],
        content="first plan",
    )

    second_prompt = save_message(
        session_id=session["id"],
        role="user",
        content="second prompt",
    )
    second_plan_message = save_message(
        session_id=session["id"],
        role="agent",
        content="second plan",
    )
    second_plan = create_plan(
        session_id=session["id"],
        prompt_message_id=second_prompt["id"],
        plan_message_id=second_plan_message["id"],
        content="second plan",
    )

    assert fetch_plan(session["id"], first_plan["id"])["status"] == "superseded"
    assert fetch_plan(session["id"], second_plan["id"])["status"] == "pending"

    approved = approve_plan(session["id"], second_plan["id"])
    assert approved["status"] == "approved"
    executed = mark_plan_executed(session["id"], second_plan["id"])
    assert executed["status"] == "executed"


def test_session_messages_include_plan_metadata(client):
    session = client.post("/sessions", json={"title": "Plan metadata"}).json()
    prompt = save_message(session_id=session["id"], role="user", content="prompt")
    plan_message = save_message(session_id=session["id"], role="agent", content="plan")
    plan = create_plan(
        session_id=session["id"],
        prompt_message_id=prompt["id"],
        plan_message_id=plan_message["id"],
        content="plan",
    )

    messages = client.get(f"/sessions/{session['id']}/messages").json()

    assert messages[0]["plan_id"] is None
    assert messages[0]["plan_status"] is None
    assert messages[1]["id"] == plan_message["id"]
    assert messages[1]["plan_id"] == plan["id"]
    assert messages[1]["plan_status"] == "pending"


def test_session_delete_cascades_plans(client):
    session = client.post("/sessions", json={"title": "Plan cascade"}).json()
    prompt = save_message(session_id=session["id"], role="user", content="prompt")
    plan_message = save_message(session_id=session["id"], role="agent", content="plan")
    plan = create_plan(
        session_id=session["id"],
        prompt_message_id=prompt["id"],
        plan_message_id=plan_message["id"],
        content="plan",
    )

    assert fetch_plan(session["id"], plan["id"])["status"] == "pending"
    assert client.delete(f"/sessions/{session['id']}").status_code == 204

    try:
        fetch_plan(session["id"], plan["id"])
    except SessionNotFoundError:
        pass
    except PlanNotFoundError:
        pass
    else:
        raise AssertionError("Plan should be removed with its session")
