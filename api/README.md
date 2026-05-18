# Automata Agent API

Small FastAPI backend used by the Tauri UI. It exposes a WebSocket endpoint that
runs a bounded agent loop against a DeepSeek-compatible LLM provider and streams
the final response back to the client.

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
automata_api/services/   Agent loop, placeholder tools, and LLM integration
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

Headless backend-only mode from the repository root:

```powershell
.\run.ps1 headless
```

This starts only the FastAPI backend in the foreground, without installing or
building the Tauri UI. It is intended for AI CLI workflows and other automated
checks that need to verify the API independently. The health endpoint is:

```text
http://127.0.0.1:8765/health
```

Override the bind address with `AUTOMATA_API_HOST` and `AUTOMATA_API_PORT` when
running multiple isolated checks.

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

The response stream starts with `started`, may include `agent_step`,
`tool_call`, and `tool_result` events while the agent loop is running, then emits
one or more `token` events followed by `done`. The built-in tools include
placeholder tools for simulated workspace inspection, code search, file reads,
patch previews, and test runs, plus real `rg`, `grep`, and `run_bash` tools.
For search, the agent should prefer `rg`; the `rg` tool falls back to `grep`,
then to `run_bash` with a suitable search command when native search commands
are unavailable. `run_bash` executes inside the workspace, uses `bash -lc`, caps
timeouts at 120 seconds, and returns stdout, stderr, exit code, timeout, and
truncation metadata.
