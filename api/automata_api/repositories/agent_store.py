from typing import Any

from automata_api.repositories import sessions as session_repository


class SessionAgentContextStore:
    def get_recent_messages(self, session_id: str, limit: int) -> list[dict[str, Any]]:
        return session_repository.get_recent_messages(session_id, limit)

    def get_messages_after_sequence(
        self, session_id: str, sequence: int
    ) -> list[dict[str, Any]]:
        return session_repository.get_messages_after_sequence(session_id, sequence)

    def get_recent_context_messages(
        self, session_id: str, limit: int
    ) -> list[dict[str, Any]]:
        return session_repository.get_recent_context_messages(session_id, limit)

    def get_context_messages_after_sequence(
        self, session_id: str, sequence: int
    ) -> list[dict[str, Any]]:
        return session_repository.get_context_messages_after_sequence(
            session_id, sequence
        )

    def save_context_message(
        self,
        session_id: str,
        message: dict[str, Any],
        *,
        source: str = "conversation",
    ) -> dict[str, Any]:
        return session_repository.save_context_message(
            session_id, message, source=source
        )

    def search_context(
        self,
        session_id: str,
        query: str,
        *,
        limit: int = 5,
        include_tool_results: bool = True,
    ) -> dict[str, Any]:
        return session_repository.search_context(
            session_id,
            query,
            limit=limit,
            include_tool_results=include_tool_results,
        )

    def fetch_context_summary(self, session_id: str) -> dict[str, Any] | None:
        return session_repository.fetch_context_summary(session_id)

    def upsert_context_summary(
        self, session_id: str, content: str, through_sequence: int
    ) -> dict[str, Any]:
        return session_repository.upsert_context_summary(
            session_id=session_id,
            content=content,
            through_sequence=through_sequence,
        )
