"""Contracts that extensions implement for the agent core.

Extensions (MCP servers, skills) must be able to advise the core about
policy without the core importing a specific extension. These protocols are
deliberately tiny: they describe the data crossing the boundary and nothing
about transport, configuration or discovery.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

ExtensionPolicyAction = Literal["allow", "prompt", "deny"]


@dataclass(frozen=True)
class ExtensionPolicyDecision:
    """What an extension wants the core to do about one call."""

    action: ExtensionPolicyAction
    reason: str


class ExtensionPolicyEvaluator(Protocol):
    """Implemented by an extension's policy engine."""

    def evaluate(
        self, *, arguments: dict[str, Any], mode: str
    ) -> ExtensionPolicyDecision: ...
