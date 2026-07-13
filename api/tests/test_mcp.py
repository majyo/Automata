import json


def write_workspace_config(workspace):
    config = workspace / ".automata" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "fake": {
                        "transport": {
                            "type": "stdio",
                            "command": "python",
                            "args": ["fake.py"],
                            "cwd": "${workspace}",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def test_mcp_server_candidate_can_be_granted_and_revoked(client, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_workspace_config(workspace)

    candidate = client.get(
        "/mcp/servers",
        params={"workspace": str(workspace)},
    ).json()[0]
    assert candidate["name"] == "fake"
    assert candidate["provenance"] == "workspace"
    assert candidate["granted"] is False

    granted = client.put(
        "/mcp/grants/fake",
        json={
            "workspace": str(workspace),
            "connection": "allow",
            "trust": "trusted",
            "default_call_policy": "allow",
        },
    ).json()
    assert granted["granted"] is True
    assert granted["fingerprint"] == candidate["fingerprint"]

    refreshed = client.get(
        "/mcp/servers",
        params={"workspace": str(workspace)},
    ).json()[0]
    assert refreshed["granted"] is True

    response = client.delete(f"/mcp/grants/{candidate['fingerprint']}")
    assert response.status_code == 204
    assert client.get(
        "/mcp/servers",
        params={"workspace": str(workspace)},
    ).json()[0]["granted"] is False


def test_mcp_server_status_reports_streamable_http_transport(client, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = workspace / ".automata" / "mcp.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "remote": {
                        "transport": {
                            "type": "streamable_http",
                            "url": "https://mcp.example.test/service",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    status = client.get(
        "/mcp/servers",
        params={"workspace": str(workspace)},
    ).json()[0]

    assert status["name"] == "remote"
    assert status["transport"] == "streamable_http"
    assert status["granted"] is False
