from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from typing import Any


def run_file_worker() -> int:
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ValueError("File worker request must be an object.")
        result = execute_file_operation(request)
        json.dump({"ok": True, "result": result}, sys.stdout, ensure_ascii=True)
        return 0
    except PermissionError:
        json.dump(
            {
                "ok": False,
                "error_code": "sandbox_denied",
                "message": "The managed sandbox denied this file operation.",
            },
            sys.stdout,
            ensure_ascii=True,
        )
        return 77
    except (OSError, TypeError, ValueError) as error:
        json.dump(
            {
                "ok": False,
                "error_code": "file_operation_failed",
                "message": str(error),
            },
            sys.stdout,
            ensure_ascii=True,
        )
        return 1


def execute_file_operation(request: dict[str, Any]) -> dict[str, Any]:
    operation = request.get("operation")
    workspace = _required_path(request, "workspace").resolve()
    path = _resolve_workspace_path(workspace, request.get("path"))

    if operation == "read":
        errors = request.get("errors", "strict")
        if errors not in {"strict", "ignore", "replace"}:
            raise ValueError("Invalid text decoding error policy.")
        return {"content": path.read_text(encoding="utf-8", errors=errors)}

    if operation == "write":
        content = request.get("content")
        mode = request.get("mode")
        if not isinstance(content, str):
            raise TypeError("File content must be a string.")
        if mode not in {"overwrite", "create", "append"}:
            raise ValueError("Invalid file write mode.")
        if bool(request.get("create_dirs", True)):
            path.parent.mkdir(parents=True, exist_ok=True)
        if mode == "append":
            with path.open("a", encoding="utf-8", newline="") as stream:
                stream.write(content)
        elif mode == "create":
            with path.open("x", encoding="utf-8", newline="") as stream:
                stream.write(content)
        else:
            path.write_text(content, encoding="utf-8", newline="")
        return {"bytes_written": len(content.encode("utf-8"))}

    if operation == "delete":
        path.unlink()
        return {}

    if operation == "stat":
        exists = path.exists()
        return {
            "exists": exists,
            "is_file": path.is_file() if exists else False,
            "is_dir": path.is_dir() if exists else False,
            "size_bytes": path.stat().st_size if exists else 0,
        }

    if operation == "parent_exists":
        return {"exists": path.parent.exists()}

    raise ValueError(f"Unsupported file worker operation: {operation!r}.")


def _required_path(request: dict[str, Any], name: str) -> Path:
    value = request.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"File worker {name} must be a non-empty string.")
    return Path(value).expanduser()


def _resolve_workspace_path(workspace: Path, value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("File worker path must be a non-empty string.")
    candidate = Path(value).expanduser()
    resolved = (
        candidate.resolve(strict=False)
        if candidate.is_absolute()
        else (workspace / candidate).resolve(strict=False)
    )
    try:
        resolved.relative_to(workspace)
    except ValueError as error:
        raise PermissionError(
            f"File operation must stay inside workspace: {workspace}"
        ) from error
    if _contains_reparse_point(workspace, resolved):
        raise PermissionError("File operation crossed a link or reparse point.")
    if resolved.exists() and resolved.is_file() and resolved.stat().st_nlink > 1:
        raise PermissionError("Managed file operations reject hard-linked files.")
    return resolved


def _contains_reparse_point(workspace: Path, path: Path) -> bool:
    relative = path.relative_to(workspace)
    current = workspace
    for part in relative.parts:
        current = current / part
        if not current.exists():
            continue
        status = current.lstat()
        if current.is_symlink():
            return True
        attributes = getattr(status, "st_file_attributes", 0)
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            return True
    return False
