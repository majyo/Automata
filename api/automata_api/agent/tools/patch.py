from typing import Any

from automata_api.agent.backends.base import Backend, BackendError

from . import _core as core
from ._core import ToolResult
from .patch_codex import (
    CodexPatchFile,
    apply_codex_hunks_to_content,
    parse_codex_patch,
)
from .base import AgentTool


class ApplyPatchTool(AgentTool):
    name = "apply_patch"

    def __init__(self, backend: Backend | None = None) -> None:
        self.backend = backend

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Apply or dry-run a real Codex-style patch inside the "
                    "workspace. The patch must start with *** Begin Patch and "
                    "end with *** End Patch. Use *** Add File, *** Update "
                    "File, and *** Delete File sections. Update hunks use @@ "
                    "without line numbers and must include enough context to "
                    "match uniquely. Use dry_run=true before applying when "
                    "practical."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "patch": {
                            "type": "string",
                            "description": (
                                "Codex-style patch text using Add File, Update "
                                "File, or Delete File sections. Example update "
                                "hunks start with @@ and lines prefixed by "
                                "space, '-', or '+'."
                            ),
                        },
                        "dry_run": {
                            "type": "boolean",
                            "description": "Validate and summarize without writing. Defaults to true.",
                        },
                        "create_dirs": {
                            "type": "boolean",
                            "description": "Create parent directories for added files. Defaults to true.",
                        },
                    },
                    "required": ["patch"],
                },
            },
        }

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        return await run_apply_patch(arguments, self.backend, tool_name=self.name)


class ApplyPatchPreviewTool(AgentTool):
    name = "apply_patch_preview"
    read_only = True

    def __init__(self, backend: Backend | None = None) -> None:
        self.backend = backend

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Dry-run a real Codex-style patch inside the workspace. "
                    "This validates and summarizes the patch without writing "
                    "files. Update hunks use @@ without line numbers and must "
                    "include enough context to match uniquely."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "patch": {
                            "type": "string",
                            "description": (
                                "Codex-style patch text using Add File, Update "
                                "File, or Delete File sections. Example update "
                                "hunks start with @@ and lines prefixed by "
                                "space, '-', or '+'."
                            ),
                        },
                    },
                    "required": ["patch"],
                },
            },
        }

    async def run(self, arguments: dict[str, Any]) -> ToolResult:
        preview_arguments = {**arguments, "dry_run": True}
        return await run_apply_patch(preview_arguments, self.backend, tool_name=self.name)


async def run_apply_patch(
    arguments: dict[str, Any],
    backend: Backend | None,
    *,
    tool_name: str = "apply_patch",
) -> ToolResult:
    if backend is None:
        raise RuntimeError("Tool instance is not bound to a backend.")

    patch = arguments.get("patch")
    dry_run = core.bool_argument(arguments, "dry_run", True)
    create_dirs = core.bool_argument(arguments, "create_dirs", True)

    if not isinstance(patch, str) or not patch.strip():
        return core.patch_error_result(
            tool_name=tool_name,
            arguments=arguments,
            dry_run=dry_run,
            error="Missing required string patch.",
        )

    if patch.strip().startswith("*** Begin Patch"):
        return await run_codex_apply_patch(
            arguments=arguments,
            backend=backend,
            tool_name=tool_name,
            patch=patch,
            dry_run=dry_run,
            create_dirs=create_dirs,
        )

    return await run_unified_apply_patch(
        arguments=arguments,
        backend=backend,
        tool_name=tool_name,
        patch=patch,
        dry_run=dry_run,
        create_dirs=create_dirs,
    )


async def run_codex_apply_patch(
    *,
    arguments: dict[str, Any],
    backend: Backend,
    tool_name: str,
    patch: str,
    dry_run: bool,
    create_dirs: bool,
) -> ToolResult:
    parsed_patch, parse_error = parse_codex_patch(patch)
    if parse_error:
        return core.patch_error_result(
            tool_name=tool_name,
            arguments=arguments,
            dry_run=dry_run,
            error=parse_error,
            syntax="codex_patch",
        )

    assert parsed_patch is not None
    planned_changes: list[dict[str, Any]] = []
    file_results: list[dict[str, Any]] = []
    for file_patch in parsed_patch.files:
        plan, error = await plan_codex_patch_file(file_patch, backend)
        if error:
            return core.patch_error_result(
                tool_name=tool_name,
                arguments=arguments,
                dry_run=dry_run,
                error=error["error"],
                path=error.get("path", ""),
                syntax="codex_patch",
            )

        assert plan is not None
        planned_changes.append(plan)
        file_results.append(
            {
                "path": plan["path"],
                "status": plan["status"],
                "hunks": len(file_patch.hunks),
                "old_lines": plan["old_lines"],
                "new_lines": plan["new_lines"],
            }
        )

    parent_error = await validate_patch_parent_dirs(
        backend, planned_changes, create_dirs=create_dirs
    )
    if parent_error and not dry_run:
        return core.patch_error_result(
            tool_name=tool_name,
            arguments=arguments,
            dry_run=dry_run,
            error=parent_error["error"],
            path=parent_error.get("path", ""),
            syntax="codex_patch",
        )

    if not dry_run:
        apply_error = await apply_planned_changes(backend, planned_changes)
        if apply_error:
            return core.patch_error_result(
                tool_name=tool_name,
                arguments=arguments,
                dry_run=dry_run,
                error=apply_error["error"],
                path=apply_error.get("path", ""),
                syntax="codex_patch",
            )

    payload = {
        "simulated": False,
        "ok": True,
        "tool": tool_name,
        "syntax": "codex_patch",
        "dry_run": dry_run,
        "files": file_results,
        "summary": core.patch_summary(file_results),
    }
    return ToolResult(
        name=tool_name,
        arguments=arguments,
        content=core.json_response(payload),
        success=True,
    )


async def run_unified_apply_patch(
    *,
    arguments: dict[str, Any],
    backend: Backend,
    tool_name: str,
    patch: str,
    dry_run: bool,
    create_dirs: bool,
) -> ToolResult:
    parsed_files, parse_error = core.parse_unified_patch(patch)
    if parse_error:
        return core.patch_error_result(
            tool_name=tool_name,
            arguments=arguments,
            dry_run=dry_run,
            error=parse_error,
            syntax="unified_diff",
        )

    planned_changes: list[dict[str, Any]] = []
    file_results: list[dict[str, Any]] = []
    for file_patch in parsed_files:
        plan, error = await plan_patch_file(file_patch, backend)
        if error:
            return core.patch_error_result(
                tool_name=tool_name,
                arguments=arguments,
                dry_run=dry_run,
                error=error["error"],
                path=error.get("path", ""),
                syntax="unified_diff",
            )

        assert plan is not None
        planned_changes.append(plan)
        file_results.append(
            {
                "path": plan["path"],
                "status": plan["status"],
                "hunks": len(file_patch.hunks),
                "old_lines": plan["old_lines"],
                "new_lines": plan["new_lines"],
            }
        )

    parent_error = await validate_patch_parent_dirs(
        backend, planned_changes, create_dirs=create_dirs
    )
    if parent_error and not dry_run:
        return core.patch_error_result(
            tool_name=tool_name,
            arguments=arguments,
            dry_run=dry_run,
            error=parent_error["error"],
            path=parent_error.get("path", ""),
            syntax="unified_diff",
        )

    if not dry_run:
        apply_error = await apply_planned_changes(backend, planned_changes)
        if apply_error:
            return core.patch_error_result(
                tool_name=tool_name,
                arguments=arguments,
                dry_run=dry_run,
                error=apply_error["error"],
                path=apply_error.get("path", ""),
                syntax="unified_diff",
            )

    payload = {
        "simulated": False,
        "ok": True,
        "tool": tool_name,
        "syntax": "unified_diff",
        "dry_run": dry_run,
        "files": file_results,
        "summary": core.patch_summary(file_results),
    }
    return ToolResult(
        name=tool_name,
        arguments=arguments,
        content=core.json_response(payload),
        success=True,
    )


async def plan_codex_patch_file(
    file_patch: CodexPatchFile, backend: Backend
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    try:
        stat = await backend.stat(file_patch.path)
    except BackendError as error:
        return None, {"path": file_patch.path, "error": str(error)}

    if file_patch.kind == "added":
        if stat.exists:
            return None, {
                "path": file_patch.path,
                "error": f"File already exists: {stat.absolute_path}",
            }
        return (
            {
                "path": file_patch.path,
                "status": "added",
                "content": file_patch.content,
                "delete_path": file_patch.path,
                "write_path": file_patch.path,
                "old_lines": 0,
                "new_lines": len(file_patch.content.splitlines()),
            },
            None,
        )

    if not stat.exists:
        return None, {
            "path": file_patch.path,
            "error": f"File does not exist: {stat.absolute_path}",
        }
    if not stat.is_file:
        return None, {
            "path": file_patch.path,
            "error": f"Path is not a file: {stat.absolute_path}",
        }

    try:
        original_content = await backend.read_file(file_patch.path, errors="strict")
    except UnicodeDecodeError:
        return None, {
            "path": file_patch.path,
            "error": f"File is not valid UTF-8 text: {stat.absolute_path}",
        }
    except BackendError as error:
        return None, {"path": file_patch.path, "error": str(error)}

    if file_patch.kind == "deleted":
        return (
            {
                "path": file_patch.path,
                "status": "deleted",
                "content": "",
                "delete_path": file_patch.path,
                "write_path": file_patch.path,
                "old_lines": len(original_content.splitlines()),
                "new_lines": 0,
            },
            None,
        )

    new_content, apply_error = apply_codex_hunks_to_content(
        original_content, file_patch.hunks, file_patch.path
    )
    if apply_error:
        return None, {"path": file_patch.path, "error": apply_error}

    write_path = file_patch.path
    display_path = file_patch.path
    if file_patch.move_path:
        try:
            move_stat = await backend.stat(file_patch.move_path)
        except BackendError as error:
            return None, {"path": file_patch.move_path, "error": str(error)}
        if move_stat.exists and move_stat.absolute_path != stat.absolute_path:
            return None, {
                "path": file_patch.move_path,
                "error": f"Destination file already exists: {move_stat.absolute_path}",
            }
        write_path = file_patch.move_path
        display_path = file_patch.move_path

    return (
        {
            "path": display_path,
            "status": "moved" if file_patch.move_path else "modified",
            "content": new_content,
            "delete_path": file_patch.path,
            "write_path": write_path,
            "old_lines": len(original_content.splitlines()),
            "new_lines": len(new_content.splitlines()),
        },
        None,
    )


async def plan_patch_file(
    file_patch: core.PatchFile, backend: Backend
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    status = core.patch_file_status(file_patch)
    if status is None:
        return None, {
            "path": file_patch.new_path or file_patch.old_path or "",
            "error": "Patch must use /dev/null for either add or delete, not both.",
        }

    relative_path = file_patch.new_path if status != "deleted" else file_patch.old_path
    if not relative_path:
        return None, {
            "path": "",
            "error": "Patch file path is missing.",
        }

    try:
        stat = await backend.stat(relative_path)
    except BackendError as error:
        return None, {"path": relative_path, "error": str(error)}

    if status == "added":
        if stat.exists:
            return None, {
                "path": relative_path,
                "error": f"File already exists: {stat.absolute_path}",
            }
        original_content = ""
    else:
        if not stat.exists:
            return None, {
                "path": relative_path,
                "error": f"File does not exist: {stat.absolute_path}",
            }
        if not stat.is_file:
            return None, {
                "path": relative_path,
                "error": f"Path is not a file: {stat.absolute_path}",
            }
        try:
            original_content = await backend.read_file(relative_path, errors="strict")
        except UnicodeDecodeError:
            return None, {
                "path": relative_path,
                "error": f"File is not valid UTF-8 text: {stat.absolute_path}",
            }
        except BackendError as error:
            return None, {"path": relative_path, "error": str(error)}

    new_content, apply_error = core.apply_hunks_to_content(
        original_content, file_patch.hunks, relative_path
    )
    if apply_error:
        return None, {"path": relative_path, "error": apply_error}

    return (
        {
            "path": relative_path,
            "status": status,
            "content": new_content,
            "delete_path": relative_path,
            "write_path": relative_path,
            "old_lines": len(original_content.splitlines()),
            "new_lines": 0 if status == "deleted" else len(new_content.splitlines()),
        },
        None,
    )


async def validate_patch_parent_dirs(
    backend: Backend,
    planned_changes: list[dict[str, Any]],
    *,
    create_dirs: bool,
) -> dict[str, str] | None:
    if create_dirs:
        return None
    for plan in planned_changes:
        if plan["status"] == "deleted":
            continue
        try:
            if not await backend.parent_exists(plan["write_path"]):
                return {
                    "path": plan["path"],
                    "error": (
                        "Parent directory does not exist: "
                        f"{backend.parent_label(plan['write_path'])}"
                    ),
                }
        except BackendError as error:
            return {"path": plan["path"], "error": str(error)}
    return None


async def apply_planned_changes(
    backend: Backend, planned_changes: list[dict[str, Any]]
) -> dict[str, str] | None:
    for plan in planned_changes:
        try:
            if plan["status"] == "deleted":
                await backend.delete_file(plan["delete_path"])
                continue

            mode = "create" if plan["status"] == "added" else "overwrite"
            await backend.write_file(
                plan["write_path"],
                plan["content"],
                mode=mode,
                create_dirs=True,
            )

            if plan["status"] == "moved" and plan["delete_path"] != plan["write_path"]:
                await backend.delete_file(plan["delete_path"])
        except BackendError as error:
            return {"path": plan["path"], "error": f"Failed to apply patch: {error}"}
    return None


apply_patch_tool = ApplyPatchTool()
apply_patch_preview_tool = ApplyPatchPreviewTool()
