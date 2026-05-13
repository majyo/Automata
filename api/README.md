# Automata Agent API

Small FastAPI backend used by the Tauri UI. It exposes a WebSocket endpoint that
streams responses from a DeepSeek-compatible LLM provider.

In the packaged desktop app, this API is built with PyInstaller and launched by
Tauri as a sidecar. The commands below are only for backend-only development.
The sidecar stores SQLite data in `AUTOMATA_DATA_DIR/automata.db`; when that
environment variable is missing, local development falls back to
`api/.data/automata.db`.

## Project layout

```text
main.py                  Thin compatibility entrypoint for uvicorn and PyInstaller
automata_api/main.py     FastAPI app factory, CORS, lifespan startup
automata_api/routers/    HTTP and WebSocket routes
automata_api/services/   Agent orchestration and LLM provider integration
automata_api/db/         SQLite connection and schema initialization
automata_api/repositories/ Session and message persistence
tests/                   FastAPI TestClient coverage
```

## LLM configuration

Create `api/.env`, create a repository-root `.env`, point `AUTOMATA_ENV_FILE`
at a dotenv file, or set process environment variables before starting the app:

```text
AUTOMATA_LLM_API_KEY=...
AUTOMATA_LLM_BASE_URL=https://api.deepseek.com
AUTOMATA_LLM_MODEL=deepseek-v4-pro
AUTOMATA_LLM_TIMEOUT_SECONDS=120
AUTOMATA_LLM_TEMPERATURE=0.2
```

`AUTOMATA_LLM_API_KEY` is required. The other values default to the DeepSeek
settings above. When running as a desktop sidecar, the API also searches upward
from the sidecar executable for `.env` and `api/.env`.

## Run

From `api/`:

```bash
uv sync
uv run uvicorn main:app --host 127.0.0.1 --port 8765 --reload
```

Or from the repository root:

```bash
uv run --directory api uvicorn main:app --host 127.0.0.1 --port 8765
```

The sidecar build uses the `build` extra:

```bash
uv run --directory api --extra build --locked pyinstaller --version
```

Tests use the `dev` dependency group:

```bash
uv run --directory api --group dev --locked pytest
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
