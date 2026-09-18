from __future__ import annotations

from automata_api.agent.extension_contracts import ExtensionPolicyDecision
from automata_api.agent.mcp.schema import McpToolMetadata
from automata_api.agent.mcp.trust import McpServerGrant


class McpPolicyEngine:
    def __init__(self, grant: McpServerGrant) -> None:
        self.grant = grant

    def evaluate(
        self,
        *,
        tool: McpToolMetadata,
        arguments: dict,
        mode: str,
    ) -> ExtensionPolicyDecision:
        del arguments
        if self.grant.connection != "allow":
            return ExtensionPolicyDecision("deny", "mcp_server_not_granted")
        if mode == "plan" and not tool.read_only:
            return ExtensionPolicyDecision("deny", "blocked_by_plan_mode")

        policy = self.grant.tool_call_policies.get(
            tool.original_name,
            self.grant.default_call_policy,
        )
        if mode == "plan" and policy == "prompt":
            return ExtensionPolicyDecision("deny", "mcp_approval_required_in_plan")
        if policy == "deny":
            return ExtensionPolicyDecision("deny", "mcp_call_rejected")
        if policy == "prompt":
            return ExtensionPolicyDecision("prompt", "mcp_approval_required")
        return ExtensionPolicyDecision("allow", "mcp_call_allowed")
