import json

from automata_api.agent.mcp.config import (
    McpStreamableHttpTransportDefinition,
    load_mcp_config,
    resolve_streamable_http_transport,
)
from automata_api.agent.mcp.trust import (
    McpTrustStore,
    create_grant,
    server_fingerprint,
)
from automata_api.agent.tools.model import ToolExposure


def write_config(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def server_payload(*, args=None, exposure="deferred", **extra):
    return {
        "transport": {
            "type": "stdio",
            "command": "python",
            "args": list(args or ["server.py"]),
            "cwd": "${workspace}",
        },
        "exposure": exposure,
        **extra,
    }


def http_server_payload(*, url, headers=None, **extra):
    return {
        "transport": {
            "type": "streamable_http",
            "url": url,
            "headers": dict(headers or {}),
        },
        **extra,
    }


def test_workspace_config_cannot_grant_or_directly_expose_server(tmp_path):
    workspace = tmp_path / "workspace"
    data_dir = tmp_path / "data"
    workspace.mkdir()
    write_config(
        workspace / ".automata" / "mcp.json",
        {
            "servers": {
                "workspace_server": server_payload(
                    exposure="direct",
                    enabled=True,
                    trusted=True,
                    approval="allow",
                )
            }
        },
    )

    result = load_mcp_config(str(workspace), data_dir=data_dir, environ={})

    assert len(result.definitions) == 1
    definition = result.definitions[0]
    assert definition.provenance == "workspace"
    assert definition.default_exposure == ToolExposure.DEFERRED
    assert any("Ignored workspace authorization fields" in item for item in result.warnings)
    assert McpTrustStore(data_dir / "mcp-grants.json").grant_for(
        definition, str(workspace)
    ) is None


def test_user_definition_overrides_workspace_definition(tmp_path):
    workspace = tmp_path / "workspace"
    data_dir = tmp_path / "data"
    workspace.mkdir()
    write_config(
        workspace / ".automata" / "mcp.json",
        {"servers": {"shared": server_payload(args=["workspace.py"]) }},
    )
    write_config(
        data_dir / "mcp.json",
        {"servers": {"shared": server_payload(args=["user.py"]) }},
    )

    result = load_mcp_config(str(workspace), data_dir=data_dir, environ={})

    assert len(result.definitions) == 1
    assert result.definitions[0].provenance == "user"
    assert result.definitions[0].transport.args == ("user.py",)


def test_config_change_invalidates_grant_fingerprint(tmp_path):
    workspace = tmp_path / "workspace"
    data_dir = tmp_path / "data"
    workspace.mkdir()
    config_path = data_dir / "mcp.json"
    write_config(
        config_path,
        {"servers": {"server": server_payload(args=["one.py"]) }},
    )
    first = load_mcp_config(str(workspace), data_dir=data_dir, environ={}).definitions[0]
    store = McpTrustStore(data_dir / "mcp-grants.json")
    store.save(
        create_grant(
            first,
            str(workspace),
            connection="allow",
            trust="trusted",
            default_call_policy="allow",
        )
    )

    write_config(
        config_path,
        {"servers": {"server": server_payload(args=["two.py"]) }},
    )
    second = load_mcp_config(str(workspace), data_dir=data_dir, environ={}).definitions[0]

    assert server_fingerprint(first, str(workspace)) != server_fingerprint(
        second, str(workspace)
    )
    assert store.grant_for(first, str(workspace)) is not None
    assert store.grant_for(second, str(workspace)) is None


def test_streamable_http_allows_https_and_resolves_header_secrets(tmp_path):
    workspace = tmp_path / "workspace"
    data_dir = tmp_path / "data"
    workspace.mkdir()
    write_config(
        data_dir / "mcp.json",
        {
            "servers": {
                "remote": http_server_payload(
                    url="https://mcp.example.test/service",
                    headers={
                        "Authorization": "Bearer ${MCP_TEST_TOKEN}",
                        "X-Workspace": "${workspace}",
                    },
                )
            }
        },
    )

    definition = load_mcp_config(
        str(workspace),
        data_dir=data_dir,
        environ={},
    ).definitions[0]
    resolved = resolve_streamable_http_transport(
        definition,
        str(workspace),
        environ={"MCP_TEST_TOKEN": "secret-value"},
    )

    assert isinstance(definition.transport, McpStreamableHttpTransportDefinition)
    assert resolved.url == "https://mcp.example.test/service"
    assert resolved.headers == {
        "Authorization": "Bearer secret-value",
        "X-Workspace": str(workspace.resolve()),
    }


def test_streamable_http_rejects_remote_plaintext_and_managed_headers(tmp_path):
    workspace = tmp_path / "workspace"
    data_dir = tmp_path / "data"
    workspace.mkdir()
    write_config(
        data_dir / "mcp.json",
        {
            "servers": {
                "plaintext": http_server_payload(url="http://example.test/mcp"),
                "managed-header": http_server_payload(
                    url="https://example.test/mcp",
                    headers={"MCP-Session-Id": "forged"},
                ),
                "literal-secret": http_server_payload(
                    url="https://example.test/mcp",
                    headers={"Authorization": "Bearer plaintext-token"},
                ),
                "loopback": http_server_payload(url="http://127.0.0.1:9000/mcp"),
            }
        },
    )

    result = load_mcp_config(str(workspace), data_dir=data_dir, environ={})

    assert [definition.name for definition in result.definitions] == ["loopback"]
    assert any("must use HTTPS" in warning for warning in result.warnings)
    assert any("managed by Automata" in warning for warning in result.warnings)
    assert any("must use an environment variable reference" in warning for warning in result.warnings)


def test_streamable_http_rejects_newlines_from_resolved_header_secret(tmp_path):
    workspace = tmp_path / "workspace"
    data_dir = tmp_path / "data"
    workspace.mkdir()
    write_config(
        data_dir / "mcp.json",
        {
            "servers": {
                "remote": http_server_payload(
                    url="https://mcp.example.test/service",
                    headers={"Authorization": "Bearer ${MCP_TEST_TOKEN}"},
                )
            }
        },
    )
    definition = load_mcp_config(
        str(workspace),
        data_dir=data_dir,
        environ={},
    ).definitions[0]

    try:
        resolve_streamable_http_transport(
            definition,
            str(workspace),
            environ={"MCP_TEST_TOKEN": "token\r\nX-Forged: yes"},
        )
    except ValueError as error:
        assert "Invalid HTTP header value" in str(error)
    else:
        raise AssertionError("resolved header injection was not rejected")
