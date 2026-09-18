"""Import-graph scanner used by the architecture boundary tests.

The scanner is intentionally dependency free (stdlib ``ast`` only) so the
architecture suite can always run, and it understands the three import
shapes that the boundary rules must cover:

* absolute imports (``from automata_api.runs import api``)
* relative imports (``from ..storage import sqlite``)
* imports nested inside functions or methods

Type-checking-only references (``if TYPE_CHECKING:``) are reported
separately because they cannot cause a runtime dependency.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "automata_api"
BASELINE_PATH = Path(__file__).resolve().parent / "boundary_baseline.json"
PROJECT_PACKAGE = "automata_api"


@dataclass(frozen=True)
class ImportRef:
    """One import statement found in a source file."""

    source_file: str
    source_module: str
    imported_module: str
    line: int
    nested: bool
    type_checking_only: bool

    @property
    def edge(self) -> str:
        return f"{self.source_module} -> {self.imported_module}"


def _module_name_for(path: Path) -> str:
    """Return the dotted source module label used for baseline entries."""
    relative = path.relative_to(PACKAGE_ROOT)
    if relative.name == "__init__.py":
        parts = relative.parts[:-1] or ("__init__",)
        return ".".join(parts) if parts else "<root>"
    return ".".join(relative.with_suffix("").parts)


def _resolve_relative(node: ast.ImportFrom, path: Path) -> str | None:
    if node.level == 0:
        return node.module

    # ``from .x import y`` inside ``a/b/c.py`` resolves against ``a.b``;
    # inside ``a/b/__init__.py`` it also resolves against ``a.b``.
    package_parts = list(path.relative_to(PACKAGE_ROOT).parts[:-1])
    ascend = node.level - 1
    if ascend:
        package_parts = package_parts[: len(package_parts) - ascend]
    base = ".".join(package_parts)
    if node.module:
        base = f"{base}.{node.module}" if base else node.module
    return f"{PROJECT_PACKAGE}.{base}" if base else PROJECT_PACKAGE


class _ImportCollector(ast.NodeVisitor):
    """Collect every import, tracking nesting and TYPE_CHECKING guards."""

    def __init__(self, path: Path, source_module: str) -> None:
        self.path = path
        self.source_module = source_module
        self.refs: list[ImportRef] = []
        self._function_depth = 0
        self._type_checking_depth = 0

    # -- scope tracking -------------------------------------------------
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking_guard(node.test):
            self._type_checking_depth += 1
            for child in node.body:
                self.visit(child)
            self._type_checking_depth -= 1
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    # -- imports --------------------------------------------------------
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._record(alias.name, node.lineno)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        resolved = _resolve_relative(node, self.path)
        if resolved:
            self._record(resolved, node.lineno, level=node.level)

    def _record(self, name: str, line: int, *, level: int = 0) -> None:
        if not name:
            return
        root = name.split(".")[0]
        if root != PROJECT_PACKAGE:
            return
        self.refs.append(
            ImportRef(
                source_file=self.path.relative_to(PACKAGE_ROOT.parent).as_posix(),
                source_module=self.source_module,
                imported_module=name,
                line=line,
                nested=self._function_depth > 0,
                type_checking_only=self._type_checking_depth > 0,
            )
        )


def _is_type_checking_guard(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def collect_imports(package_root: Path | None = None) -> list[ImportRef]:
    """Return every intra-project import reference in the package."""
    root = package_root or PACKAGE_ROOT
    refs: list[ImportRef] = []
    for path in sorted(root.rglob("*.py")):
        source_module = _module_name_for(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        collector = _ImportCollector(path, source_module)
        collector.visit(tree)
        refs.extend(collector.refs)
    return refs


def source_group(source_module: str) -> str:
    """Collapse a source module to the coarse group used for reporting."""
    return source_module.split(".")[0]


def imported_group(imported_module: str) -> str:
    """Collapse an imported module to the package it belongs to."""
    parts = imported_module.split(".")
    return parts[1] if len(parts) > 1 else "<root>"


def cross_module_edges(refs: list[ImportRef]) -> set[str]:
    """Return coarse ``source -> imported package`` edges, runtime only."""
    edges: set[str] = set()
    for ref in refs:
        if ref.type_checking_only:
            continue
        left = source_group(ref.source_module)
        right = imported_group(ref.imported_module)
        if left != right:
            edges.add(f"{left} -> {right}")
    return edges


def load_baseline(path: Path | None = None) -> set[str]:
    payload = json.loads((path or BASELINE_PATH).read_text(encoding="utf-8"))
    return set(payload["violations"])


def runtime_edges_by_file(refs: list[ImportRef]) -> dict[str, set[str]]:
    """Map each source file to the project modules it imports at runtime."""
    graph: dict[str, set[str]] = {}
    for ref in refs:
        if ref.type_checking_only:
            continue
        graph.setdefault(ref.source_file, set()).add(ref.imported_module)
    return graph


def find_import_cycles(refs: list[ImportRef]) -> list[list[str]]:
    """Detect cycles between project sub-packages (coarse module graph)."""
    return _find_cycles(_package_graph(refs))


def prohibited_cycles(refs: list[ImportRef]) -> list[list[str]]:
    """Cycles that are architecturally forbidden rather than declared.

    Plan section 3.3 fixes the allowed direction of every cross-package
    edge, so a coarse cycle is only a defect when none of its edges is
    already a forbidden edge. For example ``extensions -> agent.tools`` is
    the documented way an extension plugs in, so
    ``agent.tools -> ... -> extensions -> agent.tools`` is expected, whereas
    ``agent -> extensions`` must not exist at all and is reported by
    :func:`find_forbidden_edges` with a far clearer message.
    """
    cycles: list[list[str]] = []
    for cycle in find_import_cycles(refs):
        edges = list(zip(cycle, cycle[1:], strict=False))
        if any(_is_forbidden_edge(source, target) for source, target in edges):
            continue
        cycles.append(cycle)
    return cycles


def _package_graph(refs: list[ImportRef]) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for ref in refs:
        if ref.type_checking_only:
            continue
        left = source_group(ref.source_module)
        right = imported_group(ref.imported_module)
        if left != right:
            graph.setdefault(left, set()).add(right)
    return graph


# Packages whose dependency on the agent core is the whole point of the
# extension mechanism, and therefore allowed to close a cycle with it.
EXTENSION_PACKAGES = frozenset({"extensions"})


def _is_forbidden_edge(source: str, target: str) -> bool:
    """Return whether ``source -> target`` breaks a layering rule."""
    if source in {"agent", "runs", "sessions"} and target in EXTENSION_PACKAGES:
        return True
    if source in {"workspace", "execution"} and target in {"agent", "tools"}:
        return True
    if source in {"agent", "runs", "sessions"} and target in {
        "routers",
        "services",
        "transport",
    }:
        return True
    return False


def find_forbidden_edges(refs: list[ImportRef]) -> set[str]:
    """Return every real cross-package edge that violates a layering rule."""
    offenders: set[str] = set()
    for ref in refs:
        if ref.type_checking_only:
            continue
        source = source_group(ref.source_module)
        target = imported_group(ref.imported_module)
        if source == target:
            continue
        if _is_forbidden_edge(source, target):
            offenders.add(f"{source} -> {target}")
    return offenders


def _find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    cycles: list[list[str]] = []
    seen: set[frozenset[str]] = set()

    def walk(node: str, stack: list[str]) -> None:
        if node in stack:
            cycle = stack[stack.index(node) :]
            key = frozenset(cycle)
            if key not in seen:
                seen.add(key)
                cycles.append([*cycle, node])
            return
        for neighbour in sorted(graph.get(node, ())):
            walk(neighbour, [*stack, node])

    for start in sorted(graph):
        walk(start, [])
    return cycles
