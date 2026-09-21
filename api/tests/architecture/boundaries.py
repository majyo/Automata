"""Import-graph scanner used by the architecture boundary tests.

The scanner is intentionally dependency free (stdlib ``ast`` only) so the
architecture suite can always run, and it understands the three import
shapes that the boundary rules must cover:

* absolute imports (``from automata_api.core.runs import api``)
* relative imports (``from ..storage import sqlite``)
* imports nested inside functions or methods

Type-checking-only references (``if TYPE_CHECKING:``) are reported
separately because they cannot cause a runtime dependency.
"""

from __future__ import annotations

import ast
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
            for alias in node.names:
                candidate = resolved + "." + alias.name
                relative = candidate.removeprefix(PROJECT_PACKAGE + ".")
                target = PACKAGE_ROOT.joinpath(*relative.split("."))
                name = (
                    candidate
                    if target.with_suffix(".py").exists()
                    or (target / "__init__.py").exists()
                    else resolved
                )
                self._record(name, node.lineno, level=node.level)

    def _record(self, name: str, line: int, *, level: int = 0) -> None:
        if not name:
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


def layer_violations(refs: list[ImportRef]) -> list[ImportRef]:
    violations = []
    forbidden_external = {"fastapi", "httpx", "sqlite3", "subprocess", "mcp"}
    for ref in refs:
        source = ref.source_module.split(".")[0]
        parts = ref.imported_module.split(".")
        target = parts[1] if parts[0] == PROJECT_PACKAGE and len(parts) > 1 else None
        if source == "core" and (
            target in {"infrastructure", "bootstrap", "transport", "main"}
            or parts[0] in forbidden_external
        ):
            violations.append(ref)
        elif source == "infrastructure" and target in {
            "bootstrap",
            "transport",
            "main",
        }:
            violations.append(ref)
        elif source == "transport" and target == "infrastructure":
            violations.append(ref)
    return violations
