# Automata Demo API

Small FastAPI backend used by the Tauri UI demo. It exposes a WebSocket endpoint
that streams preset fake agent replies.

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
