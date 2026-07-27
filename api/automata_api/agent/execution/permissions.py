from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

PermissionPreset = Literal["default", "full_access"]
ApprovalPolicy = Literal["on_request", "never"]
SandboxEnforcement = Literal["managed", "disabled", "external"]
NetworkSandboxPolicy = Literal["restricted", "enabled"]
FileAccess = Literal["deny", "read", "write"]
FileSystemPolicyKind = Literal["restricted", "unrestricted", "external"]

DEFAULT_PERMISSION_PRESET: PermissionPreset = "default"
PERMISSION_PROFILE_VERSION = 1
ENVIRONMENT_POLICY_VERSION = 1
PROTECTED_METADATA_NAMES = (".git", ".automata", ".agents")


@dataclass(frozen=True)
class FileSystemRule:
    path: str
    access: FileAccess

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "access": self.access}


@dataclass(frozen=True)
class FileSystemPolicy:
    kind: FileSystemPolicyKind
    entries: tuple[FileSystemRule, ...]
    glob_scan_max_depth: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "entries": [entry.to_dict() for entry in self.entries],
            "glob_scan_max_depth": self.glob_scan_max_depth,
        }


@dataclass(frozen=True)
class RuntimePermissions:
    preset: PermissionPreset
    approval_policy: ApprovalPolicy
    sandbox_enforcement: SandboxEnforcement
    file_system: FileSystemPolicy
    network: NetworkSandboxPolicy
    environment_policy_version: int = ENVIRONMENT_POLICY_VERSION


@dataclass(frozen=True)
class CompiledPermissionProfile:
    version: int
    preset: PermissionPreset
    approval_policy: ApprovalPolicy
    sandbox_enforcement: SandboxEnforcement
    file_system: FileSystemPolicy
    network: NetworkSandboxPolicy
    environment_policy_version: int
    workspace_roots: tuple[str, ...]
    temporary_roots: tuple[str, ...]
    runtime_roots: tuple[str, ...]
    protected_paths: tuple[str, ...]
    deny_read_paths: tuple[str, ...]
    profile_hash: str

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "version": self.version,
            "preset": self.preset,
            "approval_policy": self.approval_policy,
            "sandbox_enforcement": self.sandbox_enforcement,
            "file_system": self.file_system.to_dict(),
            "network": self.network,
            "environment_policy_version": self.environment_policy_version,
            "workspace_roots": list(self.workspace_roots),
            "temporary_roots": list(self.temporary_roots),
            "runtime_roots": list(self.runtime_roots),
            "protected_paths": list(self.protected_paths),
            "deny_read_paths": list(self.deny_read_paths),
        }
        if include_hash:
            payload["profile_hash"] = self.profile_hash
        return payload

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )


_PRESETS: dict[PermissionPreset, RuntimePermissions] = {
    "default": RuntimePermissions(
        preset="default",
        approval_policy="on_request",
        sandbox_enforcement="managed",
        file_system=FileSystemPolicy(kind="restricted", entries=()),
        network="restricted",
    ),
    "full_access": RuntimePermissions(
        preset="full_access",
        approval_policy="never",
        sandbox_enforcement="disabled",
        file_system=FileSystemPolicy(kind="unrestricted", entries=()),
        network="enabled",
    ),
}


def normalize_permission_preset(value: object) -> PermissionPreset:
    if isinstance(value, str):
        normalized = value.strip().lower().replace("-", "_")
        if normalized in _PRESETS:
            return cast(PermissionPreset, normalized)
    raise ValueError("Permission preset must be one of: default, full_access.")


def permissions_for_preset(value: object) -> RuntimePermissions:
    return _PRESETS[normalize_permission_preset(value)]


def compile_permission_profile(
    value: object,
    *,
    workspace: str | Path,
    sensitive_paths: tuple[str | Path, ...] = (),
    temporary_paths: tuple[str | Path, ...] | None = None,
    runtime_paths: tuple[str | Path, ...] | None = None,
) -> CompiledPermissionProfile:
    permissions = permissions_for_preset(value)
    workspace_path = _canonical_path(workspace)
    workspace_roots = (str(workspace_path),)
    temporary_roots = tuple(
        dict.fromkeys(
            str(_canonical_path(path))
            for path in (
                temporary_paths
                if temporary_paths is not None
                else (tempfile.gettempdir(),)
            )
        )
    )
    runtime_roots = tuple(
        dict.fromkeys(
            str(_canonical_path(path))
            for path in (
                runtime_paths
                if runtime_paths is not None
                else _default_runtime_paths()
            )
            if Path(path).exists()
        )
    )
    protected_paths = tuple(
        str(workspace_path / name) for name in PROTECTED_METADATA_NAMES
    )
    deny_read_paths = tuple(
        dict.fromkeys(str(_canonical_path(path)) for path in sensitive_paths)
    )

    entries: tuple[FileSystemRule, ...]
    if permissions.file_system.kind == "unrestricted":
        entries = ()
    else:
        root = Path(workspace_path.anchor or os.sep)
        entries = (
            FileSystemRule(str(root), "read"),
            *(FileSystemRule(path, "write") for path in workspace_roots),
            *(FileSystemRule(path, "write") for path in temporary_roots),
            *(FileSystemRule(path, "deny") for path in protected_paths),
            *(FileSystemRule(path, "deny") for path in deny_read_paths),
        )

    file_system = FileSystemPolicy(
        kind=permissions.file_system.kind,
        entries=entries,
        glob_scan_max_depth=permissions.file_system.glob_scan_max_depth,
    )
    unsigned = {
        "version": PERMISSION_PROFILE_VERSION,
        "preset": permissions.preset,
        "approval_policy": permissions.approval_policy,
        "sandbox_enforcement": permissions.sandbox_enforcement,
        "file_system": file_system.to_dict(),
        "network": permissions.network,
        "environment_policy_version": permissions.environment_policy_version,
        "workspace_roots": list(workspace_roots),
        "temporary_roots": list(temporary_roots),
        "runtime_roots": list(runtime_roots),
        "protected_paths": list(protected_paths),
        "deny_read_paths": list(deny_read_paths),
    }
    profile_hash = _profile_hash(unsigned)
    return CompiledPermissionProfile(
        version=PERMISSION_PROFILE_VERSION,
        preset=permissions.preset,
        approval_policy=permissions.approval_policy,
        sandbox_enforcement=permissions.sandbox_enforcement,
        file_system=file_system,
        network=permissions.network,
        environment_policy_version=permissions.environment_policy_version,
        workspace_roots=workspace_roots,
        temporary_roots=temporary_roots,
        runtime_roots=runtime_roots,
        protected_paths=protected_paths,
        deny_read_paths=deny_read_paths,
        profile_hash=profile_hash,
    )


def compile_run_permission_profile(
    value: object,
    *,
    workspace: str | Path,
    run_id: str | None = None,
) -> CompiledPermissionProfile:
    from automata_api.config import env_file_candidates, get_database_config

    sensitive: list[Path] = [get_database_config().path.parent]
    sensitive.extend(path for path in env_file_candidates() if path.exists())
    temporary_paths: tuple[Path, ...] | None = None
    if run_id is not None:
        run_key = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:32]
        run_temp = Path(tempfile.gettempdir()) / "automata-sandbox" / "runs" / run_key
        run_temp.mkdir(parents=True, exist_ok=True)
        temporary_paths = (run_temp,)
    return compile_permission_profile(
        value,
        workspace=workspace,
        sensitive_paths=tuple(sensitive),
        temporary_paths=temporary_paths,
    )


def permission_profile_from_json(payload_json: str) -> CompiledPermissionProfile:
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as error:
        raise ValueError("Permission profile is not valid JSON.") from error
    if not isinstance(payload, dict):
        raise ValueError("Permission profile must be a JSON object.")
    version = payload.get("version")
    if version != PERMISSION_PROFILE_VERSION:
        raise ValueError(f"Unsupported permission profile version: {version!r}.")

    preset = normalize_permission_preset(payload.get("preset"))
    expected = permissions_for_preset(preset)
    if payload.get("approval_policy") != expected.approval_policy:
        raise ValueError("Permission profile approval policy does not match preset.")
    if payload.get("sandbox_enforcement") != expected.sandbox_enforcement:
        raise ValueError("Permission profile sandbox enforcement does not match preset.")
    if payload.get("network") != expected.network:
        raise ValueError("Permission profile network policy does not match preset.")

    file_system_payload = payload.get("file_system")
    if not isinstance(file_system_payload, dict):
        raise ValueError("Permission profile file_system must be an object.")
    kind = file_system_payload.get("kind")
    if kind not in {"restricted", "unrestricted", "external"}:
        raise ValueError("Permission profile contains an invalid filesystem kind.")
    raw_entries = file_system_payload.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError("Permission profile filesystem entries must be a list.")
    entries: list[FileSystemRule] = []
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            raise ValueError("Permission profile filesystem entry must be an object.")
        path = raw_entry.get("path")
        access = raw_entry.get("access")
        if not isinstance(path, str) or access not in {"deny", "read", "write"}:
            raise ValueError("Permission profile contains an invalid filesystem entry.")
        entries.append(FileSystemRule(path=path, access=cast(FileAccess, access)))

    unsigned = {key: value for key, value in payload.items() if key != "profile_hash"}
    profile_hash = payload.get("profile_hash")
    if not isinstance(profile_hash, str) or profile_hash != _profile_hash(unsigned):
        raise ValueError("Permission profile hash is invalid.")

    return CompiledPermissionProfile(
        version=PERMISSION_PROFILE_VERSION,
        preset=preset,
        approval_policy=expected.approval_policy,
        sandbox_enforcement=expected.sandbox_enforcement,
        file_system=FileSystemPolicy(
            kind=cast(FileSystemPolicyKind, kind),
            entries=tuple(entries),
            glob_scan_max_depth=_optional_int(
                file_system_payload.get("glob_scan_max_depth")
            ),
        ),
        network=expected.network,
        environment_policy_version=_required_int(
            payload.get("environment_policy_version"),
            "environment_policy_version",
        ),
        workspace_roots=_string_tuple(payload.get("workspace_roots"), "workspace_roots"),
        temporary_roots=_string_tuple(payload.get("temporary_roots"), "temporary_roots"),
        runtime_roots=_string_tuple(payload.get("runtime_roots"), "runtime_roots"),
        protected_paths=_string_tuple(payload.get("protected_paths"), "protected_paths"),
        deny_read_paths=_string_tuple(payload.get("deny_read_paths"), "deny_read_paths"),
        profile_hash=profile_hash,
    )


def sandbox_backend_for_profile(
    profile: CompiledPermissionProfile,
    *,
    platform: str | None = None,
) -> str:
    if profile.sandbox_enforcement == "disabled":
        return "direct"
    resolved_platform = platform or sys.platform
    if resolved_platform == "win32":
        return "windows-appcontainer"
    if resolved_platform == "darwin":
        return "macos-seatbelt"
    if resolved_platform.startswith("linux"):
        return "linux-bwrap"
    if profile.sandbox_enforcement == "external":
        return "external"
    return "unsupported"


def _canonical_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _default_runtime_paths() -> tuple[Path, ...]:
    from automata_api.agent.execution.sandbox.runtime_paths import (
        managed_runtime_roots,
    )

    return managed_runtime_roots()


def _profile_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _string_tuple(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Permission profile {name} must be a string list.")
    return tuple(value)


def _required_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"Permission profile {name} must be an integer.")
    return value


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return _required_int(value, "glob_scan_max_depth")
