from __future__ import annotations

from automata_api.agent.mcp.schema import (
    McpPolicyDecision,
    McpToolMetadata,
)
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
    ) -> McpPolicyDecision:
        del arguments
        if self.grant.connection != "allow":
            return McpPolicyDecision("deny", "mcp_server_not_granted")
        if mode == "plan" and not tool.read_only:
            return McpPolicyDecision("deny", "blocked_by_plan_mode")

        policy = self.grant.tool_call_policies.get(
            tool.original_name,
            self.grant.default_call_policy,
        )
        if mode == "plan" and policy == "prompt":
            return McpPolicyDecision("deny", "mcp_approval_required_in_plan")
        if policy == "deny":
            return McpPolicyDecision("deny", "mcp_call_rejected")
        if policy == "prompt":
            return McpPolicyDecision("prompt", "mcp_approval_required")
        return McpPolicyDecision("allow", "mcp_call_allowed")
