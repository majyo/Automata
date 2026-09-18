"""Execution module: processes, output and platform sandboxing.

The permission vocabulary lives here because it is platform policy rather
than an agent concern: sessions store presets, runs snapshot compiled
profiles, and tools read the resulting execution constraints. This package
is intentionally importable without loading the agent runtime.
"""

from automata_api.execution.permissions import (
    CompiledPermissionProfile,
    PermissionPreset,
    RuntimePermissions,
    compile_run_permission_profile,
    normalize_permission_preset,
    permission_profile_from_json,
    permissions_for_preset,
    sandbox_backend_for_profile,
)

__all__ = [
    "CompiledPermissionProfile",
    "PermissionPreset",
    "RuntimePermissions",
    "compile_run_permission_profile",
    "normalize_permission_preset",
    "permission_profile_from_json",
    "permissions_for_preset",
    "sandbox_backend_for_profile",
]
