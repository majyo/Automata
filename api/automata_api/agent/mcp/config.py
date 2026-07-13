from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping
from urllib.parse import urlparse

from automata_api.agent.tools.model import ToolExposure
from automata_api.config import api_dir, get_database_config


McpConfigProvenance = Literal["explicit", "user", "workspace", "packaged"]
McpTransportType = Literal["stdio", "streamable_http"]
_WORKSPACE_TOKEN = "${workspace}"
_SECRET_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_PROTECTED_HTTP_HEADERS = {
    "accept",
    "connection",
    "content-length",
    "content-type",
    "host",
    "mcp-protocol-version",
    "mcp-session-id",
    "origin",
    "transfer-encoding",
}
_SENSITIVE_HTTP_HEADERS = {
    "api-key",
    "authorization",
    "cookie",
    "proxy-authorization",
    "x-api-key",
}


@dataclass(frozen=True)
class McpStdioTransportDefinition:
    command: str
    args: tuple[str, ...] = ()
    cwd: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class McpStreamableHttpTransportDefinition:
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)


McpTransportDefinition = (
    McpStdioTransportDefinition | McpStreamableHttpTransportDefinition
)


@dataclass(frozen=True)
class McpToolOverride:
    read_only: bool | None = None
    exposure: ToolExposure | None = None


@dataclass(frozen=True)
class McpServerDefinition:
    name: str
    transport: McpTransportDefinition
    provenance: McpConfigProvenance
    source_path: str
    default_exposure: ToolExposure = ToolExposure.DEFERRED
    tool_overrides: Mapping[str, McpToolOverride] = field(default_factory=dict)
    list_timeout_seconds: float = 10.0
    call_timeout_seconds: float = 60.0


@dataclass(frozen=True)
class McpConfigLoadResult:
    definitions: tuple[McpServerDefinition, ...]
    warnings: tuple[str, ...] = ()


def load_mcp_config(
    workspace: str,
    *,
    environ: Mapping[str, str] | None = None,
    data_dir: Path | None = None,
) -> McpConfigLoadResult:
    env = os.environ if environ is None else environ
    workspace_path = Path(workspace).expanduser().resolve()
    user_data_dir = data_dir or get_database_config().path.parent
    explicit = str(env.get("AUTOMATA_MCP_CONFIG", "")).strip()
    candidates: list[tuple[Path, McpConfigProvenance]] = [
        (api_dir() / "mcp.json", "packaged"),
        (workspace_path / ".automata" / "mcp.json", "workspace"),
        (user_data_dir / "mcp.json", "user"),
    ]
    if explicit:
        candidates.append((Path(explicit).expanduser(), "explicit"))

    definitions_by_name: dict[str, McpServerDefinition] = {}
    warnings: list[str] = []
    seen_paths: set[Path] = set()
    for raw_path, provenance in candidates:
        path = raw_path.resolve()
        if path in seen_paths or not path.exists():
            continue
        seen_paths.add(path)
        loaded, file_warnings = _load_config_file(
            path,
            provenance=provenance,
            workspace=workspace_path,
        )
        warnings.extend(file_warnings)
        for definition in loaded:
            existing = definitions_by_name.get(definition.name)
            if existing is not None and provenance == "workspace":
                warnings.append(
                    f"Workspace MCP server {definition.name!r} cannot override "
                    f"{existing.provenance} definition."
                )
                continue
            if existing is not None and existing.provenance in {"user", "explicit"}:
                if provenance not in {"user", "explicit"}:
                    warnings.append(
                        f"Ignored lower-trust MCP server definition {definition.name!r}."
                    )
                    continue
            definitions_by_name[definition.name] = definition

    return McpConfigLoadResult(
        definitions=tuple(definitions_by_name.values()),
        warnings=tuple(warnings),
    )


def resolve_stdio_transport(
    definition: McpServerDefinition,
    workspace: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> McpStdioTransportDefinition:
    if not isinstance(definition.transport, McpStdioTransportDefinition):
        raise TypeError(f"MCP server {definition.name!r} is not a stdio server")
    env = os.environ if environ is None else environ
    normalized_workspace = str(Path(workspace).expanduser().resolve())

    def replace_workspace(value: str) -> str:
        return value.replace(_WORKSPACE_TOKEN, normalized_workspace)

    resolved_env: dict[str, str] = {}
    for key, value in definition.transport.env.items():
        resolved_env[key] = _resolve_config_value(
            value,
            definition_name=definition.name,
            workspace=normalized_workspace,
            environ=env,
        )

    return McpStdioTransportDefinition(
        command=replace_workspace(definition.transport.command),
        args=tuple(replace_workspace(value) for value in definition.transport.args),
        cwd=replace_workspace(definition.transport.cwd or normalized_workspace),
        env=resolved_env,
    )


def resolve_streamable_http_transport(
    definition: McpServerDefinition,
    workspace: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> McpStreamableHttpTransportDefinition:
    if not isinstance(definition.transport, McpStreamableHttpTransportDefinition):
        raise TypeError(
            f"MCP server {definition.name!r} is not a Streamable HTTP server"
        )
    env = os.environ if environ is None else environ
    normalized_workspace = str(Path(workspace).expanduser().resolve())
    headers: dict[str, str] = {}
    for key, value in definition.transport.headers.items():
        resolved_value = _resolve_config_value(
            value,
            definition_name=definition.name,
            workspace=normalized_workspace,
            environ=env,
        )
        if "\r" in resolved_value or "\n" in resolved_value:
            raise ValueError(f"Invalid HTTP header value for MCP server {definition.name!r}")
        headers[key] = resolved_value
    return McpStreamableHttpTransportDefinition(
        url=definition.transport.url,
        headers=headers,
    )


def transport_type(definition: McpServerDefinition) -> McpTransportType:
    if isinstance(definition.transport, McpStdioTransportDefinition):
        return "stdio"
    return "streamable_http"


def is_remote_http_transport(definition: McpServerDefinition) -> bool:
    if not isinstance(definition.transport, McpStreamableHttpTransportDefinition):
        return False
    hostname = urlparse(definition.transport.url).hostname
    return hostname is None or not _is_loopback_host(hostname)


def _load_config_file(
    path: Path,
    *,
    provenance: McpConfigProvenance,
    workspace: Path,
) -> tuple[list[McpServerDefinition], list[str]]:
    warnings: list[str] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [], [f"Failed to read MCP config {path}: {error}"]
    if not isinstance(payload, dict) or not isinstance(payload.get("servers"), dict):
        return [], [f"MCP config {path} must contain an object named 'servers'."]

    definitions: list[McpServerDefinition] = []
    for name, raw_server in payload["servers"].items():
        if not isinstance(name, str) or not name.strip() or not isinstance(raw_server, dict):
            warnings.append(f"Ignored invalid MCP server entry in {path}.")
            continue
        if provenance == "workspace":
            ignored = sorted(
                key
                for key in ("enabled", "approval", "trusted")
                if key in raw_server
            )
            if ignored:
                warnings.append(
                    f"Ignored workspace authorization fields for MCP server "
                    f"{name!r}: {', '.join(ignored)}."
                )
        try:
            definitions.append(
                _parse_server_definition(
                    name.strip(),
                    raw_server,
                    provenance=provenance,
                    source_path=path,
                    workspace=workspace,
                )
            )
        except (TypeError, ValueError) as error:
            warnings.append(f"Ignored MCP server {name!r} in {path}: {error}")
    return definitions, warnings


def _parse_server_definition(
    name: str,
    payload: dict[str, Any],
    *,
    provenance: McpConfigProvenance,
    source_path: Path,
    workspace: Path,
) -> McpServerDefinition:
    raw_transport = payload.get("transport")
    if not isinstance(raw_transport, dict):
        raise ValueError("transport must be an object")
    raw_transport_type = raw_transport.get("type", "stdio")
    if raw_transport_type == "stdio":
        transport = _parse_stdio_transport(raw_transport)
    elif raw_transport_type in {"streamable_http", "streamable-http"}:
        transport = _parse_streamable_http_transport(raw_transport)
    else:
        raise ValueError(f"unsupported MCP transport: {raw_transport_type!r}")

    default_exposure = _parse_exposure(
        payload.get("exposure", ToolExposure.DEFERRED.value)
    )
    if provenance == "workspace" and default_exposure == ToolExposure.DIRECT:
        default_exposure = ToolExposure.DEFERRED

    overrides: dict[str, McpToolOverride] = {}
    raw_tools = payload.get("tools", {})
    if not isinstance(raw_tools, dict):
        raise ValueError("tools must be an object")
    for tool_name, raw_override in raw_tools.items():
        if not isinstance(tool_name, str) or not isinstance(raw_override, dict):
            raise ValueError("tool overrides must be named objects")
        read_only = raw_override.get("read_only")
        if read_only is not None and not isinstance(read_only, bool):
            raise ValueError(f"read_only override for {tool_name!r} must be boolean")
        exposure = None
        if "exposure" in raw_override:
            exposure = _parse_exposure(raw_override["exposure"])
            if provenance == "workspace" and exposure == ToolExposure.DIRECT:
                exposure = ToolExposure.DEFERRED
        overrides[tool_name] = McpToolOverride(
            read_only=read_only,
            exposure=exposure,
        )

    return McpServerDefinition(
        name=name,
        transport=transport,
        provenance=provenance,
        source_path=str(source_path),
        default_exposure=default_exposure,
        tool_overrides=overrides,
        list_timeout_seconds=_positive_float(
            payload.get("list_timeout_seconds", 10.0),
            name="list_timeout_seconds",
            maximum=60.0,
        ),
        call_timeout_seconds=_positive_float(
            payload.get("call_timeout_seconds", 60.0),
            name="call_timeout_seconds",
            maximum=600.0,
        ),
    )


def _parse_stdio_transport(payload: dict[str, Any]) -> McpStdioTransportDefinition:
    command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("stdio command must be a non-empty string")
    args = payload.get("args", [])
    if not isinstance(args, list) or not all(isinstance(value, str) for value in args):
        raise ValueError("stdio args must be an array of strings")
    raw_env = payload.get("env", {})
    if not isinstance(raw_env, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in raw_env.items()
    ):
        raise ValueError("stdio env must be a string map")
    cwd = payload.get("cwd", _WORKSPACE_TOKEN)
    if cwd is not None and not isinstance(cwd, str):
        raise ValueError("stdio cwd must be a string")
    return McpStdioTransportDefinition(
        command=command.strip(),
        args=tuple(args),
        cwd=cwd,
        env=dict(raw_env),
    )


def _parse_streamable_http_transport(
    payload: dict[str, Any],
) -> McpStreamableHttpTransportDefinition:
    url = payload.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Streamable HTTP url must be a non-empty string")
    normalized_url = url.strip()
    _validate_streamable_http_url(normalized_url)
    raw_headers = payload.get("headers", {})
    if not isinstance(raw_headers, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in raw_headers.items()
    ):
        raise ValueError("Streamable HTTP headers must be a string map")
    headers: dict[str, str] = {}
    seen_names: set[str] = set()
    for name, value in raw_headers.items():
        normalized_name = name.strip()
        lowered_name = normalized_name.lower()
        if not normalized_name or not _HEADER_NAME_PATTERN.fullmatch(normalized_name):
            raise ValueError(f"invalid HTTP header name: {name!r}")
        if lowered_name in _PROTECTED_HTTP_HEADERS:
            raise ValueError(f"HTTP header {normalized_name!r} is managed by Automata")
        if lowered_name in seen_names:
            raise ValueError(f"duplicate HTTP header name: {normalized_name!r}")
        if "\r" in value or "\n" in value:
            raise ValueError(f"invalid HTTP header value for {normalized_name!r}")
        if (
            lowered_name in _SENSITIVE_HTTP_HEADERS
            and not _SECRET_PATTERN.search(value)
        ):
            raise ValueError(
                f"sensitive HTTP header {normalized_name!r} must use an "
                "environment variable reference"
            )
        seen_names.add(lowered_name)
        headers[normalized_name] = value
    return McpStreamableHttpTransportDefinition(
        url=normalized_url,
        headers=headers,
    )


def _validate_streamable_http_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Streamable HTTP url must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Streamable HTTP url must not contain credentials")
    if parsed.fragment:
        raise ValueError("Streamable HTTP url must not contain a fragment")
    if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname):
        raise ValueError("remote Streamable HTTP servers must use HTTPS")


def _is_loopback_host(hostname: str) -> bool:
    normalized = hostname.rstrip(".").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _resolve_config_value(
    value: str,
    *,
    definition_name: str,
    workspace: str,
    environ: Mapping[str, str],
) -> str:
    with_workspace = value.replace(_WORKSPACE_TOKEN, workspace)

    def replace_secret(match: re.Match[str]) -> str:
        name = match.group(1)
        if name == "workspace":
            return workspace
        if name not in environ:
            raise ValueError(
                f"Missing environment variable for MCP server "
                f"{definition_name!r}: {name}"
            )
        return str(environ[name])

    return _SECRET_PATTERN.sub(replace_secret, with_workspace)


def _parse_exposure(value: Any) -> ToolExposure:
    try:
        return ToolExposure(str(value))
    except ValueError as error:
        raise ValueError(f"invalid tool exposure: {value!r}") from error


def _positive_float(value: Any, *, name: str, maximum: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a number") from error
    if parsed <= 0 or parsed > maximum:
        raise ValueError(f"{name} must be greater than 0 and at most {maximum}")
    return parsed
