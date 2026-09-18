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
    find_import_cycles,
    load_baseline,
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
    cycles = find_import_cycles(refs)
    unexpected = [cycle for cycle in cycles if frozenset(cycle) not in KNOWN_CYCLES]

    assert unexpected == [], f"New import cycles detected: {unexpected!r}"


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
