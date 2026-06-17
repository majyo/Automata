from typing import Any

from ._core import ToolResult, run_apply_patch
from .base import AgentTool


class ApplyPatchTool(AgentTool):
    name = "apply_patch"

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

    async def run(self, arguments: dict[str, Any], workspace: str) -> ToolResult:
        return run_apply_patch(arguments, workspace, tool_name=self.name)


class ApplyPatchPreviewTool(AgentTool):
    name = "apply_patch_preview"

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

    async def run(self, arguments: dict[str, Any], workspace: str) -> ToolResult:
        preview_arguments = {**arguments, "dry_run": True}
        return run_apply_patch(preview_arguments, workspace, tool_name=self.name)


apply_patch_tool = ApplyPatchTool()
apply_patch_preview_tool = ApplyPatchPreviewTool()
