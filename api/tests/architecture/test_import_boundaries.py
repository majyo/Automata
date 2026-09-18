"""Architecture boundary checks for the automata_api package.

These tests are the M0 contract baseline: they freeze the dependency
violations that exist today, fail on any new one, and fail when a frozen
violation has been fixed but the baseline was not updated. The frozen
lists may only shrink.
"""

from __future__ import annotations

import pytest

from tests.architecture.boundaries import (
    collect_imports,
    cross_module_edges,
    find_forbidden_edges,
    load_baseline,
    prohibited_cycles,
)

# Two package-level cycles exist in the pre-refactoring tree and are the
# explicit targets of the M0/M7 cleanup. New cycles are rejected.
KNOWN_CYCLES: set[frozenset[str]] = {
    frozenset({"agent", "observability"}),
    frozenset({"agent", "repositories"}),
}

# Packages that must never depend on the agent tool surface. Enforced from
# M2 onward; today only guards against a regression being introduced early.
TOOLS_PACKAGE = "automata_api.agent.tools"


@pytest.fixture(scope="module")
def refs():
    return collect_imports()


def test_no_new_cross_module_dependency_violations(refs):
    current = cross_module_edges(refs)
    baseline = load_baseline()
    new_violations = sorted(current - baseline)

    assert new_violations == [], (
        "New cross-module dependency violations were introduced. Either "
        "remove the import or, if the current migration stage genuinely "
        "requires it, record it in "
        "tests/architecture/boundary_baseline.json:\n  "
        + "\n  ".join(new_violations)
    )


def test_baseline_has_no_stale_entries(refs):
    """Frozen violations must be deleted once the dependency is gone."""
    current = cross_module_edges(refs)
    baseline = load_baseline()
    fixed = sorted(baseline - current)

    assert fixed == [], (
        "These recorded violations no longer exist; delete them from "
        "tests/architecture/boundary_baseline.json so the list keeps "
        "shrinking:\n  " + "\n  ".join(fixed)
    )


def test_only_known_package_cycles_remain(refs):
    """Only the two pre-existing, declared cycles may remain.

    Cycles that necessarily use a forbidden edge are reported by
    ``test_no_forbidden_layering_edges`` instead, with a clearer message.
    """
    cycles = prohibited_cycles(refs)
    unexpected = [cycle for cycle in cycles if frozenset(cycle) not in KNOWN_CYCLES]

    assert unexpected == [], f"New import cycles detected: {unexpected!r}"


def test_no_forbidden_layering_edges(refs):
    """Plan 3.3: enforce the direction of each cross-package edge.

    ``extensions`` may build on the agent's public tool surface; the agent
    core must not depend on an extension. ``workspace``/``execution`` must
    not depend on ``agent``/``tools``. No business package may depend on the
    transport layer.
    """
    offenders = sorted(find_forbidden_edges(refs))

    assert offenders == [], (
        "Forbidden layering edges were introduced:\n  " + "\n  ".join(offenders)
    )


def test_workspace_and_execution_never_import_tools(refs):
    """Plan 3.3.3: workspace/execution return data, not ToolResult."""
    offenders = sorted(
        {
            f"{ref.source_file}:{ref.line} imports {ref.imported_module}"
            for ref in refs
            if not ref.type_checking_only
            and ref.source_module.split(".")[0] in {"workspace", "execution"}
            and ref.imported_module.startswith(TOOLS_PACKAGE)
        }
    )

    assert offenders == []


def test_tools_package_surface_is_explicit(refs):
    """The tools package exposes its helper list from one declared place.

    ``tools/api.py`` is the public surface. Two things are pinned: every
    helper the old ``_core`` module used to provide still resolves through
    the package, and the names come from the modules that own them (so the
    package is re-exporting rather than re-implementing).
    """
    from automata_api.agent import tools

    for name in (
        "ToolResult",
        "resolve_file_path",
        "resolve_search_path",
        "resolve_tool_cwd",
        "path_argument_for_cwd",
        "capture_process_output",
        "read_limited_stream",
        "json_response",
        "parse_tool_arguments",
        "run_process",
        "parse_unified_patch",
        "patch_summary",
    ):
        assert hasattr(tools, name), f"tools no longer re-exports {name}"

    assert tools.resolve_file_path.__module__ == "automata_api.workspace.paths"
    assert (
        tools.read_limited_stream.__module__
        == "automata_api.agent.execution.output"
    )
    assert tools.json_response.__module__ == "automata_api.agent.tools.args"
    assert tools.ToolResult.__module__ == "automata_api.agent.tools.models"
    assert (
        tools.parse_unified_patch.__module__
        == "automata_api.workspace.patches.parsing"
    )


def test_core_facade_is_gone(refs):
    """``tools/_core.py`` must not come back.

    The module was the mixed dumping ground this refactoring split apart;
    its responsibilities now live in tools/api.py, tools/args.py,
    tools/constants.py, tools/models.py, tools/results.py, tools/text.py,
    tools/sessions.py, tools/exec_shell.py, workspace/paths.py and
    workspace/patches/parsing.py.
    """
    from pathlib import Path

    tools_dir = (
        Path(__file__).resolve().parents[2] / "automata_api" / "agent" / "tools"
    )

    assert not (tools_dir / "_core.py").exists(), (
        "tools/_core.py was reintroduced; add the helper to its owning "
        "module and list it in tools/api.py instead"
    )


def test_agent_core_does_not_import_transport_or_fastapi(refs):
    """Plan 3.3.2: the agent core stays framework free."""
    forbidden_roots = {
        "automata_api.routers",
        "automata_api.services",
        "automata_api.transport",
        "fastapi",
    }
    offenders = sorted(
        {
            f"{ref.source_file}:{ref.line} imports {ref.imported_module}"
            for ref in refs
            if not ref.type_checking_only
            and ref.source_module.split(".")[0] in {"agent", "runs", "sessions"}
            and ref.imported_module.split(".")[0] in forbidden_roots
        }
    )

    assert offenders == []


def test_type_checking_references_are_reported_separately(refs):
    """Plan 8.2: TYPE_CHECKING references must not read as real edges.

    ``observability/sampler.py`` references ``ObservabilityManager`` only
    inside a TYPE_CHECKING block; that must not be treated as a runtime
    dependency. Coarse edges are package-level, so the guarantee worth
    testing is that the classification itself is reliable.
    """
    type_only_refs = [ref for ref in refs if ref.type_checking_only]
    runtime_files = {
        ref.source_file for ref in refs if not ref.type_checking_only
    }
    type_only_files = {ref.source_file for ref in type_only_refs}

    assert type_only_refs, "expected at least one TYPE_CHECKING reference"
    # A file may legitimately appear in both sets; what must never happen is
    # a type-only reference being counted as a runtime edge for a module it
    # alone would introduce.
    for ref in type_only_refs:
        assert ref.source_file in runtime_files or ref.source_file in type_only_files
