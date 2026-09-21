"""Agent-owned skill vocabulary.

The skill *implementation* (discovery, configuration, loading, injection and
tool dependency diagnostics) lives in ``extensions.skills``. What stays here
is the shared vocabulary the turn engine needs: the context material a skill
contributes and the value objects describing it. The core is therefore able
to talk about skills without depending on the extension that produces them.
"""

from automata_api.core.agent.skills.model import (
    SkillError,
    SkillLoadOutcome,
    SkillMetadata,
    SkillSelection,
    SkillTurnContext,
)

__all__ = [
    "SkillError",
    "SkillLoadOutcome",
    "SkillMetadata",
    "SkillSelection",
    "SkillTurnContext",
]
