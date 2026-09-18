"""Tool risk vocabulary and policy decisions.

``ToolRisk`` describes what a tool can do to the user's machine, and
``ToolPolicyDecision`` is the engine's verdict for one call. Both belong to
the tools module: the risk is a property of the tool, and the decision is
what the tool layer acts on. The run layer only cares about the approval
outcome, which it gets through the approval gateway.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ToolRisk = Literal["read", "write", "command", "destructive", "external"]
PolicyAction = Literal["allow", "prompt", "deny"]


@dataclass(frozen=True)
class ToolPolicyDecision:
    """The verdict for one tool call.

    ``approval_scope`` groups calls that may be approved together (for
    example all workspace writes), and ``allow_for_run`` records whether an
    approval may be remembered for the rest of the Run.
    """

    action: PolicyAction
    risk: ToolRisk
    reason: str
    approval_scope: str | None = None
    allow_for_run: bool = False
