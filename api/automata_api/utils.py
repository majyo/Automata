import uuid
from datetime import UTC, datetime


def normalize_title(title: str | None) -> str:
    value = (title or "New session").strip()
    return value[:80] or "New session"


def new_id() -> str:
    return uuid.uuid4().hex


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
