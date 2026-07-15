def test_health_reports_missing_llm_config(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "agent": {"status": "missing_config"},
    }
