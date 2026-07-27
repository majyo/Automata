from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast


PermissionPreset = Literal["default", "full_access"]
ApprovalPolicy = Literal["on_request", "never"]

DEFAULT_PERMISSION_PRESET: PermissionPreset = "default"


@dataclass(frozen=True)
class RuntimePermissions:
    preset: PermissionPreset
    approval_policy: ApprovalPolicy


_PRESETS: dict[PermissionPreset, RuntimePermissions] = {
    "default": RuntimePermissions(
        preset="default",
        approval_policy="on_request",
    ),
    "full_access": RuntimePermissions(
        preset="full_access",
        approval_policy="never",
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
