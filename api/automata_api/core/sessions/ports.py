"""Storage ports owned by core.sessions.

Infrastructure implements these transactional operations. Core callers do not
compose SQL or depend on a database driver.
"""

from __future__ import annotations

from typing import Any, Protocol

from automata_api.core.tools.permissions import PermissionPreset


class SessionStore(Protocol):
    """Session metadata, permission presets and backend selection."""

    def list_sessions(self) -> list[dict[str, Any]]: ...

    def create_session(
        self,
        title: str | None,
        working_directory: str | None,
        backend: str | None,
        permission_preset: PermissionPreset,
    ) -> dict[str, Any]: ...

    def update_session(
        self,
        session_id: str,
        *,
        title: str | None = None,
        permission_preset: PermissionPreset | None = None,
    ) -> dict[str, Any]: ...

    def delete_session(self, session_id: str) -> None: ...

    def session_exists(self, session_id: str) -> bool: ...

    def fetch_session(self, session_id: str) -> dict[str, Any]: ...

    def session_working_directory(self, session_id: str) -> str: ...

    def session_backend_config(self, session_id: str) -> dict[str, str]: ...


class ConversationStore(Protocol):
    """Visible messages and the hidden model context."""

    def list_messages(self, session_id: str) -> list[dict[str, Any]]: ...

    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    def save_tool_run_message(
        self,
        *,
        session_id: str,
        tool_call_id: str,
        tool: str,
        arguments: str,
    ) -> dict[str, Any]: ...

    def update_tool_run_result(
        self,
        *,
        session_id: str,
        message_id: str,
        success: bool,
        content: str,
    ) -> dict[str, Any]: ...

    def save_context_message(
        self,
        session_id: str,
        message: dict[str, Any],
        *,
        source: str = "conversation",
    ) -> dict[str, Any]: ...

    def get_recent_messages(
        self, session_id: str, limit: int
    ) -> list[dict[str, Any]]: ...

    def get_messages_after_sequence(
        self, session_id: str, sequence: int
    ) -> list[dict[str, Any]]: ...

    def get_recent_context_messages(
        self, session_id: str, limit: int
    ) -> list[dict[str, Any]]: ...

    def get_context_messages_after_sequence(
        self, session_id: str, sequence: int
    ) -> list[dict[str, Any]]: ...


class ContextStore(Protocol):
    def get_recent_messages(
        self, session_id: str, limit: int
    ) -> list[dict[str, Any]]: ...

    def get_messages_after_sequence(
        self, session_id: str, sequence: int
    ) -> list[dict[str, Any]]: ...

    def get_recent_context_messages(
        self, session_id: str, limit: int
    ) -> list[dict[str, Any]]: ...

    def get_context_messages_after_sequence(
        self, session_id: str, sequence: int
    ) -> list[dict[str, Any]]: ...

    def save_context_message(
        self,
        session_id: str,
        message: dict[str, Any],
        *,
        source: str = "conversation",
    ) -> dict[str, Any]: ...

    def search_context(
        self,
        session_id: str,
        query: str,
        *,
        limit: int = 5,
        include_tool_results: bool = True,
    ) -> dict[str, Any]: ...

    def fetch_context_summary(self, session_id: str) -> dict[str, Any] | None: ...

    def upsert_context_summary(
        self, session_id: str, content: str, through_sequence: int
    ) -> dict[str, Any]: ...
