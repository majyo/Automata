import os

from automata_api.config import get_system_prompt, workspace_dir


def agent_workspace() -> str:
    return os.environ.get("AUTOMATA_WORKSPACE_DIR") or str(workspace_dir())


def approved_plan_message(content: str) -> dict[str, str]:
    return {
        "role": "system",
        "content": (
            "The user has approved the following implementation plan. Execute "
            "the user's requested work according to this plan. If repository "
            "facts conflict with the plan, prefer the repository facts and "
            "explain the adjustment in the final response.\n\n"
            f"{content}"
        ),
    }


def plan_system_prompt(workspace: str | None = None) -> str:
    current_workspace = workspace or agent_workspace()
    return (
        f"{get_system_prompt()}\n\n"
        f"Current workspace: {current_workspace}\n\n"
        "You are in backend Plan mode. Your job is to create a concrete, "
        "implementation-ready Markdown plan for the user's request. Inspect "
        "the workspace with read-only tools as needed, but do not execute the "
        "implementation.\n\n"
        "Allowed tools in Plan mode are read_file, rg, grep, and "
        "apply_patch_preview. Do not call run_bash, write_file, or apply_patch "
        "in Plan mode.\n\n"
        "Return only the plan content. Include enough detail that a later "
        "approved execution can follow it without asking the user to choose "
        "between implementation options."
    )


def agent_system_prompt(workspace: str | None = None) -> str:
    current_workspace = workspace or agent_workspace()
    return (
        f"{get_system_prompt()}\n\n"
        f"Current workspace: {current_workspace}\n\n"
        "You can use run_bash to execute real bash commands inside the "
        "workspace. run_bash results have simulated=false and may have command "
        "side effects. Prefer run_bash for checks and tests.\n\n"
        "For code or text search, prefer the rg tool first. It automatically "
        "falls back to grep and then to run_bash when needed. Use grep directly "
        "only when grep behavior is specifically required.\n\n"
        "Use read_file to inspect exact file contents and write_file only when "
        "the user explicitly asks you to create or change files. Both operate "
        "on real workspace files and return simulated=false.\n\n"
        "Use apply_patch for targeted code edits with unified diffs. When "
        "practical, call apply_patch with dry_run=true before applying changes "
        "with dry_run=false. Only claim files were changed after apply_patch or "
        "write_file returns simulated=false and ok=true."
    )
