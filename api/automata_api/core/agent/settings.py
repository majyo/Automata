"""Execution settings contain no credentials or client implementation."""

from dataclasses import dataclass, field

from automata_api.config import (
    DEFAULT_AGENT_MAX_STEPS,
    DEFAULT_LLM_MODEL,
    ContextCompressionConfig,
)


@dataclass(frozen=True)
class TurnSettings:
    model: str = DEFAULT_LLM_MODEL
    max_steps: int = DEFAULT_AGENT_MAX_STEPS
    compression: ContextCompressionConfig = field(
        default_factory=lambda: ContextCompressionConfig(
            enabled=False, threshold_chars=10_000, target_chars=1_000
        )
    )
