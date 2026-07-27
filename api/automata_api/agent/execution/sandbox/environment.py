from __future__ import annotations

import os
from collections.abc import Mapping

from automata_api.agent.execution.permissions import CompiledPermissionProfile

_SAFE_EXACT = {
    "ALLUSERSPROFILE",
    "APPDATA",
    "CARGO_HOME",
    "COLORTERM",
    "COMMONPROGRAMFILES",
    "COMMONPROGRAMFILES(X86)",
    "COMMONPROGRAMW6432",
    "COMSPEC",
    "GOPATH",
    "GOROOT",
    "HOMEDRIVE",
    "HOMEPATH",
    "HOME",
    "JAVA_HOME",
    "LANG",
    "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "PATH",
    "PATHEXT",
    "PNPM_HOME",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMW6432",
    "PYTHONIOENCODING",
    "RUSTUP_HOME",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TERM",
    "TMP",
    "TMPDIR",
    "USERDOMAIN",
    "USERNAME",
    "USERPROFILE",
    "VIRTUAL_ENV",
    "WINDIR",
}
_SAFE_PREFIXES = ("LC_", "PROCESSOR_", "NPM_", "NODE_", "UV_")
_SECRET_MARKERS = (
    "API_KEY",
    "AUTHORIZATION",
    "BEARER",
    "COOKIE",
    "CREDENTIAL",
    "PASSWORD",
    "PRIVATE_KEY",
    "SECRET",
    "TOKEN",
)


def build_tool_environment(
    profile: CompiledPermissionProfile,
    supplied: Mapping[str, str] | None = None,
    *,
    explicit_names: tuple[str, ...] = (),
) -> dict[str, str]:
    source = dict(os.environ if supplied is None else supplied)
    result: dict[str, str] = {}
    explicit = {name.upper() for name in explicit_names}
    for name, value in source.items():
        normalized = name.upper()
        if (
            normalized not in explicit
            and any(marker in normalized for marker in _SECRET_MARKERS)
        ):
            continue
        if (
            normalized in explicit
            or normalized in _SAFE_EXACT
            or normalized.startswith(_SAFE_PREFIXES)
        ):
            result[name] = value
    result["AUTOMATA_SANDBOX"] = (
        "0" if profile.sandbox_enforcement == "disabled" else "1"
    )
    result["AUTOMATA_PERMISSION_PROFILE_HASH"] = profile.profile_hash
    result["AUTOMATA_NETWORK_POLICY"] = profile.network
    return result
