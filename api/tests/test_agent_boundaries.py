import ast
from pathlib import Path


FORBIDDEN_IMPORTS = (
    "automata_api.services",
    "automata_api.routers",
    "fastapi",
)


def test_agent_package_does_not_depend_on_api_transport_layers():
    agent_dir = Path(__file__).resolve().parents[1] / "automata_api" / "agent"
    violations = []

    for path in sorted(agent_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            for imported_name in imported_module_names(node):
                if is_forbidden_import(imported_name):
                    violations.append(f"{path.relative_to(agent_dir)}: {imported_name}")

    assert violations == []


def imported_module_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        return [node.module] if node.module else []

    return []


def is_forbidden_import(imported_name: str) -> bool:
    return any(
        imported_name == forbidden or imported_name.startswith(f"{forbidden}.")
        for forbidden in FORBIDDEN_IMPORTS
    )
