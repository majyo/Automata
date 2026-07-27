from __future__ import annotations

import json
import os
from pathlib import Path

import uvicorn
from mcp import types
from mcp.server.fastmcp import FastMCP
from starlette.responses import RedirectResponse

request_log = Path(os.environ["FAKE_MCP_HTTP_REQUEST_LOG"])
call_log = Path(os.environ["FAKE_MCP_HTTP_CALL_LOG"])
server = FastMCP(
    "automata-http-test",
    host="127.0.0.1",
    port=int(os.environ["FAKE_MCP_HTTP_PORT"]),
    streamable_http_path="/mcp",
    json_response=os.environ.get("FAKE_MCP_HTTP_JSON_RESPONSE", "0") == "1",
)


@server.tool(
    annotations=types.ToolAnnotations(readOnlyHint=True),
    structured_output=True,
)
def echo_remote(value: str) -> dict[str, str]:
    """Echo a value over Streamable HTTP."""
    call_log.write_text(
        json.dumps({"name": "echo_remote", "arguments": {"value": value}}),
        encoding="utf-8",
    )
    return {"echo": value}


@server.custom_route("/redirect", methods=["GET", "POST", "DELETE"])
async def redirect_to_mcp(request):
    del request
    return RedirectResponse("/mcp", status_code=307)


class RequestRecorder:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in scope.get("headers", [])
            }
            with request_log.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "method": scope["method"],
                            "path": scope["path"],
                            "authorization": headers.get("authorization"),
                            "session_id": headers.get("mcp-session-id"),
                            "protocol_version": headers.get("mcp-protocol-version"),
                        }
                    )
                    + "\n"
                )
        await self.app(scope, receive, send)


if __name__ == "__main__":
    uvicorn.run(
        RequestRecorder(server.streamable_http_app()),
        host="127.0.0.1",
        port=int(os.environ["FAKE_MCP_HTTP_PORT"]),
        log_level="error",
    )
