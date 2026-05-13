def test_health_reports_missing_llm_config(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "agent": {
            "status": "missing_config",
            "message": (
                "Missing AUTOMATA_LLM_API_KEY. Add it to api/.env, .env, "
                "AUTOMATA_ENV_FILE, or the process environment."
            ),
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-pro",
        },
    }
