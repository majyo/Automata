"""SQLite adapters for core.sessions storage ports.

Session and conversation operations reuse the SQL functions in this package;
context operations share the same connection and transaction conventions.
Bootstrap injects these adapters into business services.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from automata_api.core.sessions.search import ContextSearchError
from automata_api.core.tools.permissions import PermissionPreset
from automata_api.infrastructure.persistence import sessions as session_repository


class SqliteSessionStore:
    """Session metadata and permission presets."""

    def list_sessions(self) -> list[dict[str, Any]]:
        return session_repository.list_sessions()

    def create_session(
        self,
        title: str | None,
        working_directory: str | None,
        backend: str | None,
        permission_preset: PermissionPreset,
    ) -> dict[str, Any]:
        return session_repository.create_session(
            title, working_directory, backend, permission_preset
        )

    def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        permission_preset: PermissionPreset | None = None,
    ) -> dict[str, Any]:
        return session_repository.update_session(
            session_id, title=title, permission_preset=permission_preset
        )

    def delete_session(self, session_id: str) -> None:
        session_repository.delete_session(session_id)

    def session_exists(self, session_id: str) -> bool:
        return session_repository.session_exists(session_id)

    def fetch_session(self, session_id: str) -> dict[str, Any]:
        return session_repository.fetch_session(session_id)  # type: ignore[return-value]

    def session_working_directory(self, session_id: str) -> str:
        return session_repository.session_working_directory(session_id)

    def session_backend_config(self, session_id: str) -> dict[str, str]:
        return session_repository.session_backend_config(session_id)


class SqliteConversationStore:
    """Visible messages plus the hidden model context."""

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        return session_repository.list_messages(session_id)

    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return session_repository.save_message(
            session_id, role, content, metadata=metadata
        )

    def save_tool_run_message(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        tool: str,
        arguments: str,
    ) -> dict[str, Any]:
        return session_repository.save_tool_run_message(
            session_id=session_id,
            tool_call_id=tool_call_id,
            tool=tool,
            arguments=arguments,
        )

    def update_tool_run_result(
        self,
        *,
        session_id: str,
        message_id: str,
        success: bool,
        content: str,
    ) -> dict[str, Any]:
        return session_repository.update_tool_run_result(
            session_id=session_id,
            message_id=message_id,
            success=success,
            content=content,
        )

    def save_context_message(
        self, session_id: str, message: dict[str, Any], *, source: str = "conversation"
    ) -> dict[str, Any]:
        return session_repository.save_context_message(
            session_id, message, source=source
        )

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


class SqliteContextStore:
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
        try:
            return session_repository.search_context(
                session_id,
                query,
                limit=limit,
                include_tool_results=include_tool_results,
            )
        except sqlite3.Error as error:
            raise ContextSearchError(str(error)) from error

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
