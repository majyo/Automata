from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

CONTEXT_SEARCH_DOCUMENTS_TABLE = "agent_context_search_documents"
CONTEXT_SEARCH_FTS_TABLE = "agent_context_search_fts"
CONTEXT_SOURCE_CONVERSATION = "conversation"
CONTEXT_SOURCE_SEARCH = "context_search"

DEFAULT_CONTEXT_SEARCH_LIMIT = 5
MAX_CONTEXT_SEARCH_LIMIT = 8
MAX_CONTEXT_SEARCH_QUERY_CHARS = 512
MAX_CONTEXT_SEARCH_RESULT_CHARS = 4_000
MAX_CONTEXT_SEARCH_SNIPPET_CHARS = 800
MAX_CONTEXT_SEARCH_CANDIDATES = 64
CONTEXT_SEARCH_CHUNK_CHARS = 4_000
CONTEXT_SEARCH_CHUNK_OVERLAP = 400
MAX_CONTEXT_SEARCHABLE_CHARS = 262_144

_CJK_RE = re.compile(r"([\u3400-\u4dbf\u4e00-\u9fff])")
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def create_context_search_schema(db: sqlite3.Connection) -> None:
    db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {CONTEXT_SEARCH_DOCUMENTS_TABLE} (
            id INTEGER PRIMARY KEY,
            context_message_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            sequence INTEGER NOT NULL,
            chunk_index INTEGER NOT NULL,
            role TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '{CONTEXT_SOURCE_CONVERSATION}',
            text TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (context_message_id)
                REFERENCES agent_context_messages(id) ON DELETE CASCADE,
            UNIQUE (context_message_id, chunk_index)
        )
        """
    )
    db.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{CONTEXT_SEARCH_DOCUMENTS_TABLE}_session_sequence
        ON {CONTEXT_SEARCH_DOCUMENTS_TABLE}(session_id, sequence)
        """
    )
    db.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS {CONTEXT_SEARCH_FTS_TABLE}
        USING fts5(normalized_text, tokenize='unicode61')
        """
    )
    db.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS {CONTEXT_SEARCH_DOCUMENTS_TABLE}_delete
        AFTER DELETE ON {CONTEXT_SEARCH_DOCUMENTS_TABLE}
        BEGIN
            DELETE FROM {CONTEXT_SEARCH_FTS_TABLE} WHERE rowid = OLD.id;
        END
        """
    )


def rebuild_context_search_index(db: sqlite3.Connection) -> None:
    create_context_search_schema(db)
    db.execute(f"DELETE FROM {CONTEXT_SEARCH_FTS_TABLE}")
    db.execute(f"DELETE FROM {CONTEXT_SEARCH_DOCUMENTS_TABLE}")
    rows = db.execute(
        """
        SELECT id, session_id, message_json, sequence, created_at, source
        FROM agent_context_messages
        ORDER BY session_id ASC, sequence ASC
        """
    ).fetchall()
    for row in rows:
        message = _decode_message(row["message_json"])
        if message is None:
            continue
        index_context_message(
            db,
            context_message_id=str(row["id"]),
            session_id=str(row["session_id"]),
            sequence=int(row["sequence"]),
            created_at=str(row["created_at"]),
            message=message,
            source=str(row["source"] or CONTEXT_SOURCE_CONVERSATION),
        )


def index_context_message(
    db: sqlite3.Connection,
    *,
    context_message_id: str,
    session_id: str,
    sequence: int,
    created_at: str,
    message: dict[str, Any],
    source: str = CONTEXT_SOURCE_CONVERSATION,
) -> None:
    create_context_search_schema(db)
    existing = db.execute(
        f"""
        SELECT id
        FROM {CONTEXT_SEARCH_DOCUMENTS_TABLE}
        WHERE context_message_id = ?
        """,
        (context_message_id,),
    ).fetchall()
    for row in existing:
        db.execute(
            f"DELETE FROM {CONTEXT_SEARCH_FTS_TABLE} WHERE rowid = ?",
            (int(row["id"]),),
        )
    db.execute(
        f"""
        DELETE FROM {CONTEXT_SEARCH_DOCUMENTS_TABLE}
        WHERE context_message_id = ?
        """,
        (context_message_id,),
    )

    if source == CONTEXT_SOURCE_SEARCH:
        return

    text = context_message_search_text(message)
    if not text:
        return

    for chunk_index, chunk in enumerate(_chunks(text)):
        cursor = db.execute(
            f"""
            INSERT INTO {CONTEXT_SEARCH_DOCUMENTS_TABLE} (
                context_message_id,
                session_id,
                sequence,
                chunk_index,
                role,
                source,
                text,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                context_message_id,
                session_id,
                sequence,
                chunk_index,
                _message_role(message),
                source,
                chunk,
                created_at,
            ),
        )
        document_id = cursor.lastrowid
        if document_id is None:
            raise RuntimeError("Failed to allocate context search document id.")
        db.execute(
            f"""
            INSERT INTO {CONTEXT_SEARCH_FTS_TABLE} (rowid, normalized_text)
            VALUES (?, ?)
            """,
            (int(document_id), normalize_for_search(chunk)),
        )


def search_context(
    db: sqlite3.Connection,
    *,
    session_id: str,
    query: str,
    limit: int = DEFAULT_CONTEXT_SEARCH_LIMIT,
    include_tool_results: bool = True,
) -> dict[str, Any]:
    if not isinstance(query, str):
        raise ValueError("query must be a string")
    query = query.strip()
    if not query:
        raise ValueError("query must not be empty")
    if len(query) > MAX_CONTEXT_SEARCH_QUERY_CHARS:
        raise ValueError(
            "query exceeds the maximum length of "
            f"{MAX_CONTEXT_SEARCH_QUERY_CHARS} characters"
        )
    if not isinstance(limit, int) or isinstance(limit, bool):
        raise ValueError("limit must be an integer")
    if not 1 <= limit <= MAX_CONTEXT_SEARCH_LIMIT:
        raise ValueError(
            f"limit must be between 1 and {MAX_CONTEXT_SEARCH_LIMIT}"
        )
    if not isinstance(include_tool_results, bool):
        raise ValueError("include_tool_results must be a boolean")

    tokens = search_tokens(query)
    if not tokens:
        return {
            "matches": [],
            "returned": 0,
            "truncated": False,
            "index": "fts5",
        }

    rows = _fts_rows(
        db,
        session_id=session_id,
        query=fts_and_query(tokens),
        include_tool_results=include_tool_results,
    )
    if not rows:
        rows = _like_rows(
            db,
            session_id=session_id,
            query=query,
            include_tool_results=include_tool_results,
        )

    distinct_message_ids = {str(row["context_message_id"]) for row in rows}
    matches: list[dict[str, Any]] = []
    seen_messages: set[str] = set()
    for row in rows:
        context_message_id = str(row["context_message_id"])
        if context_message_id in seen_messages:
            continue
        seen_messages.add(context_message_id)
        text = str(row["text"])
        score = row["score"]
        match: dict[str, Any] = {
            "context_message_id": context_message_id,
            "sequence": int(row["sequence"]),
            "role": str(row["role"]),
            "chunk_index": int(row["chunk_index"]),
            "snippet": make_snippet(text, query),
            "content": text[:MAX_CONTEXT_SEARCH_RESULT_CHARS],
            "content_truncated": len(text) > MAX_CONTEXT_SEARCH_RESULT_CHARS,
            "created_at": str(row["created_at"]),
        }
        if score is not None:
            match["score"] = round(float(score), 6)
        matches.append(match)
        if len(matches) >= limit:
            break

    return {
        "matches": matches,
        "returned": len(matches),
        "truncated": (
            len(distinct_message_ids) > limit
            or len(rows) >= MAX_CONTEXT_SEARCH_CANDIDATES
        ),
        "index": "fts5",
    }


def context_message_search_text(message: dict[str, Any]) -> str:
    parts: list[str] = []
    role = _message_role(message)
    parts.append(f"role={role}")

    content = _content_text(message.get("content"))
    if content:
        parts.append(content)

    name = message.get("name")
    if isinstance(name, str) and name.strip():
        parts.append(f"name={name.strip()}")

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            if not isinstance(function, dict):
                continue
            function_name = function.get("name")
            arguments = function.get("arguments")
            call_parts: list[str] = []
            if isinstance(function_name, str) and function_name.strip():
                call_parts.append(f"tool={function_name.strip()}")
            if isinstance(arguments, str) and arguments.strip():
                call_parts.append(f"arguments={arguments.strip()}")
            if call_parts:
                parts.append(" ".join(call_parts))

    return "\n".join(part for part in parts if part.strip())[:MAX_CONTEXT_SEARCHABLE_CHARS]


def normalize_for_search(text: str) -> str:
    return _CJK_RE.sub(r" \1 ", text)


def search_tokens(query: str) -> list[str]:
    return _TOKEN_RE.findall(normalize_for_search(query))


def fts_and_query(tokens: list[str]) -> str:
    return " AND ".join(_quote_fts_token(token) for token in tokens)


def make_snippet(text: str, query: str) -> str:
    max_chars = MAX_CONTEXT_SEARCH_SNIPPET_CHARS
    if len(text) <= max_chars:
        return text

    lower_text = text.casefold()
    positions = [lower_text.find(term.casefold()) for term in search_tokens(query)]
    positions = [position for position in positions if position >= 0]
    center = positions[0] if positions else 0
    start = max(0, center - max_chars // 3)
    end = min(len(text), start + max_chars)
    start = max(0, end - max_chars)
    snippet = text[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet += "…"
    return snippet


def _fts_rows(
    db: sqlite3.Connection,
    *,
    session_id: str,
    query: str,
    include_tool_results: bool,
) -> list[sqlite3.Row]:
    tool_filter = "" if include_tool_results else "AND documents.role <> 'tool'"
    try:
        return db.execute(
            f"""
            SELECT
                documents.context_message_id,
                documents.sequence,
                documents.chunk_index,
                documents.role,
                documents.text,
                documents.created_at,
                bm25({CONTEXT_SEARCH_FTS_TABLE}) AS score
            FROM {CONTEXT_SEARCH_FTS_TABLE}
            INNER JOIN {CONTEXT_SEARCH_DOCUMENTS_TABLE} AS documents
                ON documents.id = {CONTEXT_SEARCH_FTS_TABLE}.rowid
            WHERE {CONTEXT_SEARCH_FTS_TABLE} MATCH ?
              AND documents.session_id = ?
              AND documents.source <> ?
              {tool_filter}
            ORDER BY score ASC, documents.sequence DESC, documents.chunk_index ASC
            LIMIT ?
            """,
            (
                query,
                session_id,
                CONTEXT_SOURCE_SEARCH,
                MAX_CONTEXT_SEARCH_CANDIDATES,
            ),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def _like_rows(
    db: sqlite3.Connection,
    *,
    session_id: str,
    query: str,
    include_tool_results: bool,
) -> list[sqlite3.Row]:
    tool_filter = "" if include_tool_results else "AND role <> 'tool'"
    return db.execute(
        f"""
        SELECT
            context_message_id,
            sequence,
            chunk_index,
            role,
            text,
            created_at,
            NULL AS score
        FROM {CONTEXT_SEARCH_DOCUMENTS_TABLE}
        WHERE session_id = ?
          AND source <> ?
          AND text LIKE ? ESCAPE '\\'
          {tool_filter}
        ORDER BY sequence DESC, chunk_index ASC
        LIMIT ?
        """,
        (
            session_id,
            CONTEXT_SOURCE_SEARCH,
            _like_pattern(query),
            MAX_CONTEXT_SEARCH_CANDIDATES,
        ),
    ).fetchall()


def _like_pattern(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return f"%{escaped}%"


def _quote_fts_token(token: str) -> str:
    return '"' + token.replace('"', '""') + '"'


def _chunks(text: str) -> list[str]:
    if len(text) <= CONTEXT_SEARCH_CHUNK_CHARS:
        return [text]

    step = CONTEXT_SEARCH_CHUNK_CHARS - CONTEXT_SEARCH_CHUNK_OVERLAP
    return [
        text[start : start + CONTEXT_SEARCH_CHUNK_CHARS]
        for start in range(0, len(text), step)
    ]


def _message_role(message: dict[str, Any]) -> str:
    role = message.get("role")
    return role if isinstance(role, str) and role.strip() else "unknown"


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
            elif part.get("type") == "image_url":
                parts.append("[image]")
        return "\n".join(parts)
    return ""


def _decode_message(raw: Any) -> dict[str, Any] | None:
    try:
        value = json.loads(str(raw))
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None
