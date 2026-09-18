"""Compatibility facade for the permission vocabulary.

The canonical implementation moved to :mod:`automata_api.execution.permissions`
so that the session and run domains can depend on permission types without
importing the agent package. This module only re-exports; it owns no state
and holds no rules. It is deleted in M7 together with its callers.
"""

from __future__ import annotations

from automata_api.execution.permissions import (
    DEFAULT_PERMISSION_PRESET as DEFAULT_PERMISSION_PRESET,
)
from automata_api.execution.permissions import (
    ENVIRONMENT_POLICY_VERSION as ENVIRONMENT_POLICY_VERSION,
)
from automata_api.execution.permissions import (
    PERMISSION_PROFILE_VERSION as PERMISSION_PROFILE_VERSION,
)
from automata_api.execution.permissions import (
    PROTECTED_METADATA_NAMES as PROTECTED_METADATA_NAMES,
)
from automata_api.execution.permissions import (
    ApprovalPolicy as ApprovalPolicy,
)
from automata_api.execution.permissions import (
    CompiledPermissionProfile as CompiledPermissionProfile,
)
from automata_api.execution.permissions import FileAccess as FileAccess
from automata_api.execution.permissions import (
    FileSystemPolicy as FileSystemPolicy,
)
from automata_api.execution.permissions import (
    FileSystemPolicyKind as FileSystemPolicyKind,
)
from automata_api.execution.permissions import (
    FileSystemRule as FileSystemRule,
)
from automata_api.execution.permissions import (
    NetworkSandboxPolicy as NetworkSandboxPolicy,
)
from automata_api.execution.permissions import (
    PermissionPreset as PermissionPreset,
)
from automata_api.execution.permissions import (
    RuntimePermissions as RuntimePermissions,
)
from automata_api.execution.permissions import (
    SandboxEnforcement as SandboxEnforcement,
)
from automata_api.execution.permissions import (
    compile_permission_profile as compile_permission_profile,
)
from automata_api.execution.permissions import (
    compile_run_permission_profile as compile_run_permission_profile,
)
from automata_api.execution.permissions import (
    normalize_permission_preset as normalize_permission_preset,
)
from automata_api.execution.permissions import (
    permission_profile_from_json as permission_profile_from_json,
)
from automata_api.execution.permissions import (
    permissions_for_preset as permissions_for_preset,
)
from automata_api.execution.permissions import (
    sandbox_backend_for_profile as sandbox_backend_for_profile,
)
