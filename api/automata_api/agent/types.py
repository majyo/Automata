from collections.abc import Awaitable, Callable
from typing import Any, Protocol


EventEmitter = Callable[[dict[str, Any]], Awaitable[None]]


class AgentContextStore(Protocol):
    def get_recent_messages(self, session_id: str, limit: int) -> list[dict[str, Any]]:
        ...

    def get_messages_after_sequence(
        self, session_id: str, sequence: int
    ) -> list[dict[str, Any]]:
        ...

    def fetch_context_summary(self, session_id: str) -> dict[str, Any] | None:
        ...

    def upsert_context_summary(
        self, session_id: str, content: str, through_sequence: int
    ) -> dict[str, Any]:
        ...

