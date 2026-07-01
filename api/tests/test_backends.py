import asyncio
import os

import pytest

from automata_api.agent.backends import (
    BackendConfigurationError,
    LocalBackend,
    available_backend_kinds,
    create_backend,
    default_backend_kind,
)
from automata_api.agent.tools.registry import ToolRegistry


def test_default_backend_kind_is_available():
    assert default_backend_kind() in available_backend_kinds()


def test_create_local_backend_exposes_default_tools(tmp_path):
    backend = create_backend("local", workspace=str(tmp_path))
    registry = ToolRegistry(backend.tools())

    assert isinstance(backend, LocalBackend)
    assert {"read_file", "write_file", "apply_patch", "exec_command"} <= (
        registry.allowed_names()
    )
    assert registry.allowed_names(read_only_only=True) == {
        "read_file",
        "rg",
        "grep",
        "apply_patch_preview",
    }


def test_windows_backend_availability_matches_platform(tmp_path):
    if os.name != "nt":
        with pytest.raises(BackendConfigurationError, match="only available on Windows"):
            create_backend("windows", workspace=str(tmp_path))
        return

    backend = create_backend("windows", workspace=str(tmp_path))
    registry = ToolRegistry(backend.tools())

    assert backend.kind == "windows"
    assert "run_powershell" in registry.allowed_names()
    assert "run_powershell" not in registry.allowed_names(read_only_only=True)


def test_create_backend_rejects_unknown_kind(tmp_path):
    with pytest.raises(BackendConfigurationError, match="Unknown backend"):
        create_backend("missing", workspace=str(tmp_path))


def test_local_backend_file_primitives_reject_path_escape(tmp_path):
    backend = LocalBackend(str(tmp_path))

    asyncio.run(
        backend.write_file("nested/sample.txt", "hello\n", mode="create")
    )
    assert asyncio.run(backend.read_file("nested/sample.txt")) == "hello\n"

    with pytest.raises(Exception, match="path must stay inside workspace"):
        asyncio.run(backend.stat("../outside.txt"))
