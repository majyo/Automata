import asyncio
import json
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path

from automata_api.agent import llm, runtime
from automata_api.agent.skills.config import SkillsConfig
from automata_api.agent.skills.loader import load_skills_from_roots
from automata_api.agent.skills.manager import SkillManager, reset_skill_manager
from automata_api.agent.skills.model import SkillRoot, SkillTurnContext
from automata_api.agent.skills.runtime import (
    create_skill_turn_context,
    skill_selections_from_payload,
)
from automata_api.agent.skills.settings import SkillSettingsStore
from automata_api.config import AgentConfig, ContextCompressionConfig


def write_skill(
    root: Path,
    name: str = "code-review",
    *,
    modes: str = '["act", "plan"]',
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text(
        "\n".join(
            [
                "---",
                f'name: "{name}"',
                'description: "Review code changes and tests."',
                "metadata:",
                '  short-description: "Review workflow"',
                "---",
                "",
                "# Review",
                "",
                "Use rg before editing.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    agents = skill_dir / "agents"
    agents.mkdir()
    (agents / "openai.yaml").write_text(
        "\n".join(
            [
                "interface:",
                '  display_name: "Code Review"',
                '  short_description: "Find regressions"',
                '  brand_color: "#1F6FEB"',
                '  default_prompt: "Review the current diff."',
                "dependencies:",
                "  tools:",
                '    - type: "builtin"',
                '      value: "rg"',
                '      description: "Search files."',
                '    - type: "tool_search"',
                '      query: "github pull request review"',
                '    - type: "mcp"',
                '      server: "github"',
                '      tool: "pull_request.read"',
                "      read_only: true",
                "policy:",
                "  allow_implicit_invocation: true",
                f"  modes: {modes}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return skill_path


def test_loader_parses_skill_frontmatter_metadata_and_dependencies(tmp_path):
    skills_root = tmp_path / "skills"
    skill_path = write_skill(skills_root)

    outcome = load_skills_from_roots((SkillRoot(skills_root, "repo"),))

    assert outcome.errors == ()
    assert len(outcome.skills) == 1
    skill = outcome.skills[0]
    assert skill.name == "code-review"
    assert skill.description == "Review code changes and tests."
    assert skill.short_description == "Review workflow"
    assert skill.path == skill_path.resolve()
    assert skill.interface is not None
    assert skill.interface.display_name == "Code Review"
    assert skill.interface.brand_color == "#1F6FEB"
    assert skill.dependencies is not None
    assert [item.type for item in skill.dependencies.tools] == [
        "builtin",
        "tool_search",
        "mcp",
    ]
    assert skill.dependencies.tools[2].server == "github"
    assert skill.dependencies.tools[2].read_only is True
    assert skill.policy.modes == ("act", "plan")


def test_loader_reports_invalid_skill_without_blocking_valid_skill(tmp_path):
    skills_root = tmp_path / "skills"
    write_skill(skills_root, "valid")
    broken_dir = skills_root / "broken"
    broken_dir.mkdir()
    (broken_dir / "SKILL.md").write_text("# Missing frontmatter\n", encoding="utf-8")

    outcome = load_skills_from_roots((SkillRoot(skills_root, "repo"),))

    assert [skill.name for skill in outcome.skills] == ["valid"]
    assert len(outcome.errors) == 1
    assert "missing YAML frontmatter" in outcome.errors[0].message


def test_loader_warns_for_invalid_openai_metadata_without_dropping_skill(tmp_path):
    skills_root = tmp_path / "skills"
    skill_path = write_skill(skills_root)
    metadata_path = skill_path.parent / "agents" / "openai.yaml"
    metadata_path.write_text(
        "interface:\n  display_name: Review\n    invalid: value\n",
        encoding="utf-8",
    )

    outcome = load_skills_from_roots((SkillRoot(skills_root, "repo"),))

    assert [skill.name for skill in outcome.skills] == ["code-review"]
    assert len(outcome.errors) == 1
    assert outcome.errors[0].severity == "warning"
    assert "openai.yaml" in outcome.errors[0].message


def test_skill_id_survives_workspace_move(tmp_path):
    workspace = tmp_path / "original"
    workspace.mkdir()
    write_skill(workspace / ".automata" / "skills")
    manager = SkillManager(make_test_config(tmp_path / "data"))

    before = manager.skills_for_workspace(workspace).skills[0]
    moved_workspace = tmp_path / "moved"
    workspace.rename(moved_workspace)
    after = manager.skills_for_workspace(moved_workspace, force_reload=True).skills[0]

    assert before.skill_id == after.skill_id
    assert before.path != after.path


def test_skill_manager_cache_and_force_reload_have_observable_behavior(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill_path = write_skill(workspace / ".automata" / "skills")
    config = replace(
        make_test_config(tmp_path / "data"),
        cache_ttl_seconds=3600,
    )
    manager = SkillManager(config)

    before = manager.skills_for_workspace(workspace).skills[0]
    skill_path.write_text(
        skill_path.read_text(encoding="utf-8").replace(
            "Review code changes and tests.",
            "Review current changes safely.",
        ),
        encoding="utf-8",
    )

    cached = manager.skills_for_workspace(workspace).skills[0]
    reloaded = manager.skills_for_workspace(
        workspace,
        force_reload=True,
    ).skills[0]
    assert cached.description == before.description
    assert reloaded.description == "Review current changes safely."


def test_disabled_skill_is_excluded_from_explicit_selection(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_skill(workspace / ".automata" / "skills")
    manager = SkillManager(
        make_test_config(tmp_path / "data"),
        SkillSettingsStore(tmp_path / "skills-config.json"),
    )
    skill = manager.skills_for_workspace(workspace).skills[0]
    manager.set_enabled(workspace, skill.skill_id, enabled=False)

    context = asyncio.run(
        create_skill_turn_context(
            workspace=str(workspace),
            mode="act",
            prompt="$code-review review this change",
            manager=manager,
        )
    )

    assert context.enabled_count == 0
    assert context.selected == ()
    assert context.injected_messages == ()


def test_create_skill_turn_context_renders_and_injects_selected_skill(tmp_path):
    workspace = tmp_path / "workspace"
    repo_root = workspace / ".automata" / "skills"
    workspace.mkdir()
    skill_path = write_skill(repo_root)
    manager = SkillManager(make_test_config(tmp_path / "data"))

    context = asyncio.run(
        create_skill_turn_context(
            workspace=str(workspace),
            mode="act",
            prompt="$code-review review this change",
            selected_skills=(),
            manager=manager,
        )
    )

    assert context.loaded_count == 1
    assert context.enabled_count == 1
    assert "## Skills" in context.available_notes
    assert "code-review" in context.available_notes
    assert context.selected[0].path == skill_path.resolve()
    assert len(context.injected_messages) == 1
    assert context.injected_messages[0]["role"] == "user"
    assert "<skill>" in context.injected_messages[0]["content"]
    assert "Use rg before editing." in context.injected_messages[0]["content"]


def test_plan_mode_filters_skill_policy(tmp_path):
    workspace = tmp_path / "workspace"
    repo_root = workspace / ".automata" / "skills"
    workspace.mkdir()
    write_skill(repo_root, "act-only", modes='["act"]')
    manager = SkillManager(make_test_config(tmp_path / "data"))

    context = asyncio.run(
        create_skill_turn_context(
            workspace=str(workspace),
            mode="plan",
            prompt="$act-only make a plan",
            manager=manager,
        )
    )

    assert context.enabled_count == 0
    assert context.injected_messages == ()
    assert any("act-only" in warning for warning in context.warnings)


def test_skill_selection_payload_resolves_paths(tmp_path):
    path = tmp_path / "skill" / "SKILL.md"
    payload = [{"name": "demo", "path": str(path)}]

    selections = skill_selections_from_payload(payload)

    assert len(selections) == 1
    assert selections[0].name == "demo"
    assert selections[0].path == path.resolve()


@dataclass
class MemoryStore:
    rows: list[dict]

    def get_recent_messages(self, session_id: str, limit: int) -> list[dict]:
        return self.rows[-limit:]

    def get_messages_after_sequence(self, session_id: str, sequence: int) -> list[dict]:
        return []

    def get_recent_context_messages(self, session_id: str, limit: int) -> list[dict]:
        return self.rows[-limit:]

    def get_context_messages_after_sequence(
        self, session_id: str, sequence: int
    ) -> list[dict]:
        return []

    def save_context_message(self, session_id: str, message: dict) -> dict:
        row = {"message": message, "sequence": len(self.rows) + 1}
        self.rows.append(row)
        return row

    def fetch_context_summary(self, session_id: str) -> dict | None:
        return None

    def upsert_context_summary(
        self, session_id: str, content: str, through_sequence: int
    ) -> dict:
        return {"content": content, "through_sequence": through_sequence}


def test_runtime_inserts_skill_messages_without_persisting_them(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "get_agent_config",
        lambda: AgentConfig(
            api_key="test-key",
            base_url="https://provider.test",
            model="unit-model",
            timeout_seconds=30.0,
            temperature=0.2,
        ),
    )
    monkeypatch.setattr(
        runtime,
        "get_context_compression_config",
        lambda: ContextCompressionConfig(False, 10_000, 1_000),
    )
    calls = []

    async def fake_stream_chat_completion(messages, tools=None):
        calls.append({"messages": list(messages), "tools": tools})
        yield {"content": "done"}

    monkeypatch.setattr(llm, "stream_chat_completion", fake_stream_chat_completion)
    store = MemoryStore(rows=[{"message": {"role": "user", "content": "review"}, "sequence": 1}])

    events = asyncio.run(
        collect_events(
            runtime.stream_agent_loop(
                session_id="session",
                store=store,
                workspace="workspace",
                skill_context=SkillTurnContext(
                    available_notes="## Skills\n- code-review",
                    injected_messages=(
                        {"role": "user", "content": "<skill>body</skill>"},
                    ),
                ),
            )
        )
    )

    assert events[-1]["content"] == "done"
    assert [message["role"] for message in calls[0]["messages"]] == [
        "system",
        "user",
        "user",
    ]
    assert calls[0]["messages"][1]["content"] == "<skill>body</skill>"
    assert "## Skills" in calls[0]["messages"][0]["content"]
    assert not any(
        row["message"].get("content") == "<skill>body</skill>" for row in store.rows
    )


def test_skills_api_lists_workspace_skills(client, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_skill(workspace / ".automata" / "skills")

    response = client.get("/skills", params={"workspace": str(workspace)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["workspace"] == str(workspace.resolve())
    assert payload["skills"][0]["name"] == "code-review"
    assert payload["skills"][0]["skill_id"].startswith("skill_")
    assert payload["skills"][0]["dependencies"]["tools"][2]["server"] == "github"
    assert payload["skills"][0]["diagnostics"][0]["status"] == "available"


def test_skills_api_persists_enabled_state(client, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_skill(workspace / ".automata" / "skills")
    listed = client.get("/skills", params={"workspace": str(workspace)}).json()
    skill_id = listed["skills"][0]["skill_id"]

    disabled = client.put(
        f"/skills/{skill_id}/enabled",
        json={"workspace": str(workspace), "enabled": False},
    )

    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    reset_skill_manager()
    reloaded = client.get("/skills", params={"workspace": str(workspace)}).json()
    assert reloaded["skills"][0]["enabled"] is False
    settings = json.loads((tmp_path / "skills-config.json").read_text(encoding="utf-8"))
    assert settings["disabled"][0]["skill_id"] == skill_id


def test_skills_diagnostics_endpoint_is_advisory(client, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_skill(workspace / ".automata" / "skills")
    listed = client.get("/skills", params={"workspace": str(workspace)}).json()
    skill_id = listed["skills"][0]["skill_id"]

    response = client.get(
        f"/skills/{skill_id}/diagnostics",
        params={"workspace": str(workspace)},
    )

    assert response.status_code == 200
    statuses = {
        item["dependency_type"]: item["status"]
        for item in response.json()["diagnostics"]
    }
    assert statuses["builtin"] == "available"
    assert statuses["tool_search"] in {"not_found", "deferred"}
    assert statuses["mcp"] in {"not_found", "not_granted"}


async def collect_events(events):
    return [event async for event in events]


def make_test_config(data_dir: Path) -> SkillsConfig:
    return SkillsConfig(
        enabled=True,
        packaged_enabled=False,
        metadata_budget_chars=8_000,
        body_budget_chars=65_536,
        user_root=data_dir / "skills",
        packaged_root=data_dir / "packaged-skills",
        extra_roots=(),
    )
