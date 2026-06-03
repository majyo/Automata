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
automata_api/services/   API transport and application orchestration
automata_api/agent/      Agent runtime, context, prompts, tools, and LLM integration
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
AUTOMATA_CONTEXT_COMPRESSION_ENABLED=true
AUTOMATA_CONTEXT_COMPRESSION_THRESHOLD_CHARS=60000
AUTOMATA_CONTEXT_COMPRESSION_TARGET_CHARS=20000
```

`AUTOMATA_LLM_API_KEY` is required. The other values default to the DeepSeek
settings above. When running as a desktop sidecar, the API also searches upward
from the sidecar executable for `.env` and `api/.env`.

Context compression is enabled by default. When the provider request context
exceeds `AUTOMATA_CONTEXT_COMPRESSION_THRESHOLD_CHARS`, the same configured LLM
creates a hidden session summary targeting
`AUTOMATA_CONTEXT_COMPRESSION_TARGET_CHARS`. Visible chat messages remain
unchanged; summaries are stored separately in SQLite and are only injected into
future provider requests.

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

Existing prompt payloads run in execution mode for compatibility. To ask the
agent to prepare a persisted plan without executing mutating work, send:

```json
{ "type": "prompt", "session_id": "...", "prompt": "...", "mode": "plan" }
```

Plan mode emits the usual `started`, `agent_step`, `tool_call`, and
`tool_result` events as needed, then emits:

```json
{
  "type": "plan_ready",
  "session_id": "...",
  "plan_id": "...",
  "status": "pending",
  "content": "..."
}
```

The plan content is saved as a visible `agent` message and as a hidden
`session_plans` row. Creating a new pending plan for a session marks any older
pending plan in that session as `superseded`. To execute a pending plan, send:

```json
{ "type": "approve_plan", "session_id": "...", "plan_id": "..." }
```

Approval emits `plan_approved`, then runs the normal execution loop with the
approved plan injected into the provider context. When execution finishes, the
plan status is updated to `executed`. Invalid sessions, missing plans, and
non-pending plans emit `plan_error`.

The response stream starts with `started`, may include `agent_step`,
`context_compressed`, `tool_call`, and `tool_result` events while the agent loop
is running, then emits one or more `token` events followed by `done`.
`context_compressed` includes `scope` (`history` or `loop`), before/after
character counts, summary size, and compressed message counts. The built-in
tools are real `read_file`, `write_file`, `rg`, `grep`, `run_bash`,
`apply_patch`, and `apply_patch_preview` tools. File tools read and write UTF-8
text within the workspace only. For search, the agent should prefer `rg`; the
`rg` tool falls back to `grep`, then to `run_bash` with a suitable search
command when native search commands are unavailable. `run_bash` executes inside
the workspace, uses `bash -lc`, caps timeouts at 120 seconds, and returns
stdout, stderr, exit code, timeout, and truncation metadata.

Plan mode is enforced by the backend, not only by prompting. It exposes only
`read_file`, `rg`, `grep`, and `apply_patch_preview`. Requests for `run_bash`,
`write_file`, or `apply_patch` return a failed tool result with
`blocked_by_plan_mode` and do not execute the tool.
