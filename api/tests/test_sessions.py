from automata_api.repositories.sessions import (
    fetch_context_summary,
    list_messages,
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
