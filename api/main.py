import asyncio
import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from itertools import cycle
from pathlib import Path
from threading import Lock
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


app = FastAPI(title="Automata Agent Demo API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:1420", "http://127.0.0.1:1420", "tauri://localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


FAKE_REPLIES = [
    [
        "我会先扫描当前 workspace，确认前后端目录、可运行脚本和已有约定。\n\n",
        "从现在的结构看，第一步可以把 Python 后端固定成 FastAPI，并提供 `/health`、`/ws/chat` 两个最小入口。\n\n",
        "UI 侧保持一个很薄的 transport 层：负责连接 WebSocket、发送用户输入、接收 token 事件，然后把事件写入对话流。这样后面替换成真实 coding agent 时，只需要换后端执行器。",
    ],
    [
        "收到。我会把这个请求拆成三个执行阶段：\n\n",
        "1. 建立任务上下文：读取项目结构、识别当前分支和工作区状态。\n",
        "2. 生成执行计划：列出需要编辑的文件，并标出验证命令。\n",
        "3. 流式回传进度：每完成一个阶段就向 UI 推送事件，而不是等最终结果一次性返回。\n\n",
        "当前 demo 使用 fake reply，但事件形状会尽量贴近真实 agent。",
    ],
    [
        "这里适合保留高密度布局：左侧是会话与项目，中间是主对话流，右侧是任务、文件改动和运行状态。\n\n",
        "真正接入 agent 后，我建议消息流里混合三类事件：普通文本、工具调用状态、代码 diff 摘要。文本继续平铺，工具状态用紧凑行展示，diff 则进入右侧 inspector。",
    ],
]


reply_cycle = cycle(FAKE_REPLIES)
db_lock = Lock()


class CreateSessionRequest(BaseModel):
    title: str | None = None


class UpdateSessionRequest(BaseModel):
    title: str


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/sessions")
async def list_sessions() -> list[dict[str, Any]]:
    with db_lock, connect_db() as db:
        rows = db.execute(
            """
            SELECT
                sessions.id,
                sessions.title,
                sessions.created_at,
                sessions.updated_at,
                COUNT(messages.id) AS message_count
            FROM sessions
            LEFT JOIN messages ON messages.session_id = sessions.id
            GROUP BY sessions.id
            ORDER BY sessions.updated_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]


@app.post("/sessions", status_code=201)
async def create_session(request: CreateSessionRequest) -> dict[str, Any]:
    title = normalize_title(request.title)
    session_id = new_id()
    now = now_iso()

    with db_lock, connect_db() as db:
        db.execute(
            """
            INSERT INTO sessions (id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, title, now, now),
        )
        db.commit()

    return {
        "id": session_id,
        "title": title,
        "created_at": now,
        "updated_at": now,
        "message_count": 0,
    }


@app.patch("/sessions/{session_id}")
async def update_session(session_id: str, request: UpdateSessionRequest) -> dict[str, Any]:
    title = normalize_title(request.title)
    now = now_iso()

    with db_lock, connect_db() as db:
        cursor = db.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, session_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Session not found")
        db.commit()

        row = fetch_session(db, session_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return row


@app.delete("/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str) -> None:
    with db_lock, connect_db() as db:
        cursor = db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="Session not found")
        db.commit()


@app.get("/sessions/{session_id}/messages")
async def list_messages(session_id: str) -> list[dict[str, Any]]:
    with db_lock, connect_db() as db:
        ensure_session(db, session_id)
        rows = db.execute(
            """
            SELECT id, session_id, role, content, sequence, created_at
            FROM messages
            WHERE session_id = ?
            ORDER BY sequence ASC
            """,
            (session_id,),
        ).fetchall()
        return [dict(row) for row in rows]


@app.websocket("/ws/chat")
async def chat(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_json({"type": "ready", "message": "Fake agent backend connected"})

    try:
        while True:
            payload = await receive_payload(websocket)
            if payload.get("type") != "prompt":
                await websocket.send_json({"type": "error", "message": "Unsupported message type"})
                continue

            session_id = str(payload.get("session_id", "")).strip()
            prompt = str(payload.get("prompt", "")).strip()
            if not session_id or not prompt:
                await websocket.send_json({"type": "error", "message": "Missing session_id or prompt"})
                continue

            if not session_exists(session_id):
                await websocket.send_json({"type": "error", "message": "Session not found"})
                continue

            save_message(session_id=session_id, role="user", content=prompt)
            reply = next(reply_cycle)
            await stream_reply(websocket, session_id, prompt, reply)
    except WebSocketDisconnect:
        return


async def receive_payload(websocket: WebSocket) -> dict[str, Any]:
    message = await websocket.receive_text()
    try:
        payload = json.loads(message)
    except json.JSONDecodeError:
        return {"type": "invalid"}

    if not isinstance(payload, dict):
        return {"type": "invalid"}

    return payload


async def stream_reply(websocket: WebSocket, session_id: str, prompt: str, reply: list[str]) -> None:
    await websocket.send_json({"type": "started", "session_id": session_id, "prompt": prompt})
    response = ""

    for block in reply:
        for chunk in chunk_text(block):
            response = f"{response}{chunk}"
            await websocket.send_json({"type": "token", "content": chunk})
            await asyncio.sleep(0.045)

    message = save_message(session_id=session_id, role="agent", content=response)
    await websocket.send_json({"type": "done", "message": message})


def init_db() -> None:
    db_path().parent.mkdir(parents=True, exist_ok=True)
    with db_lock, connect_db() as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'agent')),
                content TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                UNIQUE (session_id, sequence)
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session_sequence
            ON messages(session_id, sequence);
            """
        )
        db.commit()


def connect_db() -> sqlite3.Connection:
    connection = sqlite3.connect(db_path(), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def db_path() -> Path:
    configured_dir = os.environ.get("AUTOMATA_DATA_DIR")
    if configured_dir:
        return Path(configured_dir) / "automata.db"

    return Path(__file__).resolve().parent / ".data" / "automata.db"


def fetch_session(db: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = db.execute(
        """
        SELECT
            sessions.id,
            sessions.title,
            sessions.created_at,
            sessions.updated_at,
            COUNT(messages.id) AS message_count
        FROM sessions
        LEFT JOIN messages ON messages.session_id = sessions.id
        WHERE sessions.id = ?
        GROUP BY sessions.id
        """,
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


def ensure_session(db: sqlite3.Connection, session_id: str) -> None:
    if fetch_session(db, session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")


def session_exists(session_id: str) -> bool:
    with db_lock, connect_db() as db:
        row = db.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return row is not None


def save_message(session_id: str, role: str, content: str) -> dict[str, Any]:
    message_id = new_id()
    created_at = now_iso()

    with db_lock, connect_db() as db:
        row = db.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        sequence = int(row["next_sequence"])
        db.execute(
            """
            INSERT INTO messages (id, session_id, role, content, sequence, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (message_id, session_id, role, content, sequence, created_at),
        )
        db.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (created_at, session_id))
        db.commit()

    return {
        "id": message_id,
        "session_id": session_id,
        "role": role,
        "content": content,
        "sequence": sequence,
        "created_at": created_at,
    }


def normalize_title(title: str | None) -> str:
    value = (title or "New session").strip()
    return value[:80] or "New session"


def new_id() -> str:
    return uuid.uuid4().hex


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def chunk_text(text: str, size: int = 8) -> list[str]:
    return [text[index : index + size] for index in range(0, len(text), size)]


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("AUTOMATA_API_PORT", "8765"))
    uvicorn.run(app, host="127.0.0.1", port=port, http="h11", ws="websockets", loop="asyncio")
