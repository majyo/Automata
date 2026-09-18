"""M4/M7: adding a tool must not reach outside the tools package.

The plan's completion criterion is that "adding a tool does not require
modifying the Agent main loop or the workspace backend". This pins the
structural half of that claim: the concrete builtin tool classes are
imported in exactly one place, the registry, so registering a new tool is a
change to the tools package and its registry entry.

Behavioural coverage of the tools themselves lives in tests/test_tools.py;
this file is about the wiring.
"""

from __future__ import annotations

from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "automata_api"
TOOLS = PACKAGE / "agent" / "tools"

# Modules the registry imports to declare the builtin tool set. Adding a
# builtin tool should mean adding one of these and one entry below.
TOOL_MODULES = (
    "bash",
    "exec_command",
    "files",
    "patch",
    "search",
    "write_stdin",
)

# The one module allowed to name the concrete tool classes.
REGISTRY = "registry.py"


def _importing_files(module_name: str) -> set[str]:
    """Files that import ``module_name`` from the tools package."""
    needles = (
        f"from .{module_name} import",
        f"from automata_api.agent.tools.{module_name} import",
        f"from .{module_name} import (",
    )
    found: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(needle in text for needle in needles):
            found.add(path.relative_to(PACKAGE).as_posix())
    return found


@pytest.mark.parametrize("module_name", TOOL_MODULES)
def test_concrete_tools_are_imported_only_by_the_registry(module_name):
    importers = _importing_files(module_name)

    assert importers <= {f"agent/tools/{REGISTRY}"}, (
        f"agent/tools/{module_name}.py is imported by {sorted(importers)}; "
        "adding or changing a tool should only touch the tools package and "
        "its registry entry, not the agent loop, backends or workspace"
    )


def test_registry_declares_every_registered_tool():
    """The registry's tuple is the single registration point."""
    text = (TOOLS / REGISTRY).read_text(encoding="utf-8")

    assert "REGISTERED_TOOLS: tuple[AgentTool, ...] = (" in text
    for name in (
        "rg_tool",
        "grep_tool",
        "exec_command_tool",
        "write_stdin_tool",
        "run_bash_tool",
        "read_file_tool",
        "write_file_tool",
        "apply_patch_tool",
        "apply_patch_preview_tool",
    ):
        assert name in text, f"{name} is not registered"


def test_backends_do_not_import_builtin_tools():
    """The local backend must not depend on the builtin tool modules.

    `agent/backends` may use workspace and execution capabilities, but the
    concrete tool implementations are registered above it.
    """
    offenders: list[str] = []
    for path in (PACKAGE / "agent" / "backends").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "automata_api.agent.tools import _core" in text:
            offenders.append(path.name)
    assert offenders == []


def test_tools_module_has_no_core_dumping_ground():
    """The mixed ``_core`` module was deleted and must not return."""
    assert not (TOOLS / "_core.py").exists()
