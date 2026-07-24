from typing import Any

from automata_api.agent.execution.model import ToolPolicyDecision, ToolRisk
from automata_api.agent.tools.model import ToolDescriptor


class ToolPolicyEngine:
    def evaluate(
        self,
        *,
        descriptor: ToolDescriptor,
        arguments: dict[str, Any],
        mode: str,
    ) -> ToolPolicyDecision:
        if mode == "plan" and not descriptor.read_only:
            return ToolPolicyDecision("deny", descriptor.risk, "blocked_by_plan_mode")

        executor_decision = getattr(descriptor.executor, "policy_decision", None)
        if callable(executor_decision):
            result = executor_decision(arguments, mode=mode)
            if result.action == "deny":
                return ToolPolicyDecision("deny", descriptor.risk, result.reason)
            if result.action == "prompt":
                return ToolPolicyDecision(
                    "prompt",
                    "external",
                    result.reason,
                    approval_scope=f"mcp:{descriptor.identity or descriptor.name}",
                    allow_for_run=True,
                )
            return ToolPolicyDecision("allow", descriptor.risk, result.reason)

        risk = dynamic_risk(descriptor, arguments)
        if risk == "read":
            return ToolPolicyDecision("allow", risk, "read_only_tool")
        if risk == "write":
            return ToolPolicyDecision(
                "prompt",
                risk,
                "workspace_write_requires_approval",
                approval_scope="workspace_write",
                allow_for_run=True,
            )
        if risk == "command":
            return ToolPolicyDecision("prompt", risk, "command_requires_approval")
        if risk == "destructive":
            return ToolPolicyDecision(
                "prompt", risk, "destructive_action_requires_approval"
            )
        return ToolPolicyDecision("prompt", risk, "external_action_requires_approval")


def dynamic_risk(
    descriptor: ToolDescriptor, arguments: dict[str, Any]
) -> ToolRisk:
    if descriptor.name == "write_stdin" and not arguments.get("chars"):
        return "read"
    if descriptor.name == "apply_patch":
        patch = arguments.get("patch")
        if isinstance(patch, str) and (
            "*** Delete File:" in patch or "+++ /dev/null" in patch
        ):
            return "destructive"
    return descriptor.risk
