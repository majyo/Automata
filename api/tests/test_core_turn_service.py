"""Exercise the complete core turn with memory ports and no API credentials."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from automata_api.core.agent.messages import (
    AgentProviderError,
    AssistantStreamAccumulator,
)
from automata_api.core.agent.resources import TurnResources
from automata_api.core.agent.settings import TurnSettings
from automata_api.core.agent.skills.model import SkillTurnContext
from automata_api.core.runs.approval import ApprovalBroker
from automata_api.core.runs.model import CancellationToken, PublicRunError
from automata_api.core.runs.turns import TurnService
from automata_api.core.tools.base import AgentTool
from automata_api.core.tools.models import ToolResult
from automata_api.core.tools.providers import descriptor_for_tool
from automata_api.core.tools.router import ToolRouter


class MemoryConversation:
    def __init__(self):
        self.messages = []
        self.context = []

    def get_recent_context_messages(self, session_id, limit):
        return self.context[-limit:]

    def get_recent_messages(self, session_id, limit):
        return []

    def save_context_message(self, session_id, message, *, source="conversation"):
        row = {"message": message, "sequence": len(self.context) + 1, "source": source}
        self.context.append(row)
        return row

    def save_tool_run_message(self, **values):
        row = {"id": "memory-tool-message", **values}
        self.messages.append(row)
        return row

    def update_tool_run_result(self, **values):
        self.messages[-1].update(values)
        return self.messages[-1]


class MemorySessions:
    def __init__(self, workspace):
        self.workspace = workspace
        self.calls = []

    def session_backend_config(self, session_id):
        self.calls.append(session_id)
        return {"backend": "memory", "working_directory": self.workspace}


class MemoryTool(AgentTool):
    name = "memory_read"
    read_only = True

    def spec(self):
        return {
            "type": "function",
            "function": {"name": self.name, "parameters": {"type": "object"}},
        }

    async def run(self, arguments):
        return ToolResult(self.name, arguments, "memory result", True)


class MemoryModel:
    def __init__(self, *, fail=False):
        self.calls = 0
        self.fail = fail

    def accumulator(self):
        return AssistantStreamAccumulator()

    async def stream(self, messages, *, tools):
        if self.fail:
            raise AgentProviderError("scripted failure")
        self.calls += 1
        if self.calls == 1:
            yield {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call-memory",
                        "type": "function",
                        "function": {"name": "memory_read", "arguments": "{}"},
                    }
                ]
            }
        else:
            assert messages[-1]["content"] == "memory result"
            yield {"content": "finished"}


class MemoryResources:
    def __init__(self, workspace, *, fail=False):
        self.workspace = workspace
        self.model = MemoryModel(fail=fail)
        self.closed = False

    @asynccontextmanager
    async def open(self, **values):
        assert values["session_config"]["backend"] == "memory"
        try:
            yield TurnResources(
                workspace=self.workspace,
                workspace_label="memory",
                tool_notes="",
                router=ToolRouter([descriptor_for_tool(MemoryTool())]),
                skills=SkillTurnContext(),
                provider=self.model,
                settings=TurnSettings(model="memory-model"),
            )
        finally:
            self.closed = True


class Sender:
    def __init__(self):
        self.events = []

    async def send_json(self, value):
        self.events.append(value)


@pytest.mark.parametrize("mode", ["act", "plan"])
def test_complete_turn_uses_only_injected_ports(tmp_path, monkeypatch, mode):
    monkeypatch.delenv("AUTOMATA_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    conversation = MemoryConversation()
    sessions = MemorySessions(str(tmp_path))
    resources = MemoryResources(str(tmp_path))
    service = TurnService(
        resources=resources,
        sessions=sessions,
        conversation=conversation,
        context=conversation,
    )
    sender = Sender()

    async def scenario():
        cancellation = CancellationToken()
        approval = ApprovalBroker(
            run_id="run",
            session_id="session",
            emit=sender.send_json,
            cancellation=cancellation,
        )
        args = dict(
            websocket=sender,
            session_id="session",
            prompt="read",
            run_id="run",
            cancellation=cancellation,
            approval_broker=approval,
            permission_preset="full_access",
        )
        if mode == "plan":
            return await service.stream_plan_reply(**args, prompt_message_id="prompt")
        return await service.stream_agent_reply(**args)

    outcome = asyncio.run(scenario())
    assert outcome.response_content == "finished"
    assert outcome.plan_content == ("finished" if mode == "plan" else None)
    assert sessions.calls == ["session"]
    assert conversation.messages[0]["content"] == "memory result"
    assert conversation.context[-1]["message"]["content"] == "finished"
    assert resources.closed
    assert resources.model.calls == 2


def test_turn_failure_releases_injected_resources(tmp_path):
    conversation = MemoryConversation()
    resources = MemoryResources(str(tmp_path), fail=True)
    service = TurnService(
        resources=resources,
        sessions=MemorySessions(str(tmp_path)),
        conversation=conversation,
        context=conversation,
    )
    sender = Sender()

    async def scenario():
        cancellation = CancellationToken()
        approval = ApprovalBroker(
            run_id="run",
            session_id="session",
            emit=sender.send_json,
            cancellation=cancellation,
        )
        await service.stream_agent_reply(
            sender, "session", "read", "run", cancellation, approval, "full_access"
        )

    with pytest.raises(PublicRunError) as captured:
        asyncio.run(scenario())
    assert captured.value.code == "agent_provider_error"
    assert resources.closed


def test_parallel_runs_resolve_their_own_process_managers():
    from automata_api.core.tools.process_scope import process_resources
    from automata_api.infrastructure.processes.process import get_process_supervisor
    from automata_api.infrastructure.processes.process_sessions import (
        get_process_session_manager,
    )

    async def scenario():
        async def one_run():
            supervisor, sessions = object(), object()
            with process_resources(supervisor, sessions):
                await asyncio.sleep(0)
                assert get_process_supervisor() is supervisor
                assert get_process_session_manager() is sessions
                return supervisor

        left, right = await asyncio.gather(one_run(), one_run())
        assert left is not right

    asyncio.run(scenario())
