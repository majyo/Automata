# Automata Demo API

Small FastAPI backend used by the Tauri UI demo. It exposes a WebSocket endpoint
that streams preset fake agent replies.

In the packaged desktop app, this API is built with PyInstaller and launched by
Tauri as a sidecar. The commands below are only for backend-only development.
The sidecar stores SQLite data in `AUTOMATA_DATA_DIR/automata.db`; when that
environment variable is missing, local development falls back to
`api/.data/automata.db`.

## Run

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --host 127.0.0.1 --port 8765 --reload
```

Or with `uv`:

```bash
uv run --with fastapi --with uvicorn --with websockets uvicorn main:app --host 127.0.0.1 --port 8765
```

The UI connects to:

```text
ws://127.0.0.1:8765/ws/chat
```

## Sessions

```text
GET    /sessions
POST   /sessions
PATCH  /sessions/{session_id}
DELETE /sessions/{session_id}
GET    /sessions/{session_id}/messages
```

The WebSocket prompt payload is:

```json
{ "type": "prompt", "session_id": "...", "prompt": "..." }
```
