"""Executable three-layer architecture. There is no tolerated-violation baseline."""

import ast
import subprocess
import sys

from tests.architecture.boundaries import (
    PACKAGE_ROOT,
    _ImportCollector,
    collect_imports,
    layer_violations,
)


def test_layers_have_no_outward_dependencies():
    refs = collect_imports()
    assert refs, "The scanner must inspect the real package"
    offenders = layer_violations(refs)
    assert not offenders, "\n".join(
        f"{r.source_file}:{r.line}: {r.imported_module}" for r in offenders
    )


def test_scanner_catches_absolute_relative_nested_and_type_only_imports():
    source = """
import httpx
from ...infrastructure.persistence import runs

def load():
    from automata_api.infrastructure.llm import client

if TYPE_CHECKING:
    from automata_api.transport.schemas import RunRecord
"""
    collector = _ImportCollector(PACKAGE_ROOT / "core/runs/probe.py", "core.runs.probe")
    collector.visit(ast.parse(source))
    refs = collector.refs
    assert len(layer_violations(refs)) == 4
    assert any(ref.nested for ref in refs)
    assert any(ref.type_checking_only for ref in refs)
    assert any(ref.imported_module == "httpx" for ref in refs)


def test_core_import_does_not_load_any_adapter():
    script = """
import sys
import automata_api.core.agent.runtime
import automata_api.core.runs.service
import automata_api.core.sessions.rules
bad = [name for name in sys.modules if name.startswith((
    'automata_api.infrastructure', 'automata_api.bootstrap', 'automata_api.transport',
    'httpx', 'fastapi', 'sqlite3'))]
assert not bad, bad
"""
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=PACKAGE_ROOT.parent,
        check=True,
        capture_output=True,
        text=True,
    )


def test_old_namespace_has_no_python_sources():
    for old in (
        "agent",
        "db",
        "execution",
        "extensions",
        "observability",
        "repositories",
        "routers",
        "runs",
        "services",
        "sessions",
        "storage",
        "workspace",
    ):
        assert not list((PACKAGE_ROOT / old).rglob("*.py")), old


def test_backend_contract_does_not_construct_a_tool_registry():
    source = (PACKAGE_ROOT / "core/tools/workspace.py").read_text(encoding="utf-8")
    assert "default_tools" not in source
    assert "registry" not in source


def test_sqlite_transaction_and_core_port_are_separate():
    from automata_api.core.runs.ports import RunStore
    from automata_api.infrastructure.persistence.runs import SqliteRunStore

    assert RunStore.__module__ == "automata_api.core.runs.ports"
    assert SqliteRunStore.__module__ == "automata_api.infrastructure.persistence.runs"
