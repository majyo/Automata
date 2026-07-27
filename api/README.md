# Automata Agent API

Small FastAPI backend used by the Tauri UI. It exposes a WebSocket endpoint that
runs a bounded agent loop against a DeepSeek-compatible LLM provider and streams
provider token deltas back to the client while the agent loop is running.

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
automata_api/observability/ Structured logs, spans, profile samples, and retention
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
AUTOMATA_AGENT_MAX_STEPS=24
AUTOMATA_CONTEXT_COMPRESSION_ENABLED=true
AUTOMATA_CONTEXT_MAX_TOKENS=1000000
AUTOMATA_CONTEXT_COMPRESSION_TRIGGER_RATIO=0.8
# Optional exact override; defaults to max tokens * trigger ratio * 4 chars/token.
# AUTOMATA_CONTEXT_COMPRESSION_THRESHOLD_CHARS=3200000
AUTOMATA_CONTEXT_COMPRESSION_TARGET_CHARS=20000
```

`AUTOMATA_LLM_API_KEY` is required. The other values default to the DeepSeek
settings above. When running as a desktop sidecar, the API also searches upward
from the sidecar executable for `.env` and `api/.env`.

`AUTOMATA_AGENT_MAX_STEPS` bounds the number of model rounds in one agent run,
including both tool-calling rounds and the final response round. It defaults to
24 to allow multi-tool tasks to finish while still stopping runaway loops.

Context compression is enabled by default. The default compression trigger is
derived from a 1,000,000-token model context limit, a trigger ratio of `0.8`,
and an estimate of 4 characters per token, resulting in a default threshold of
3,200,000 characters. Override `AUTOMATA_CONTEXT_MAX_TOKENS` or
`AUTOMATA_CONTEXT_COMPRESSION_TRIGGER_RATIO` to tune that derived threshold, or
set `AUTOMATA_CONTEXT_COMPRESSION_THRESHOLD_CHARS` for an exact character
limit. When the provider request context exceeds the resolved threshold, the
same configured LLM creates a hidden session summary targeting
`AUTOMATA_CONTEXT_COMPRESSION_TARGET_CHARS`. Visible chat messages remain
unchanged; summaries are stored separately in SQLite and are only injected into
future provider requests.

## Run

Headless backend-only mode from the repository root:

```powershell
$env:AUTOMATA_API_TOKEN = '<at-least-32-random-characters>'
.\run.ps1 headless
```

Diagnostic observability is always enabled. Explicit profile starts are:

```powershell
.\run.ps1 -Mode headless -Profile
.\run.ps1 -Mode headless -Profile -ProfileCaptureContent
```

`-ProfileCaptureContent` is rejected unless `-Profile` is also present. Profile
mode is fixed for the lifetime of the backend process and cannot be enabled by
an API or WebSocket request.

This starts only the FastAPI backend in the foreground, without installing or
building the Tauri UI. It is intended for AI CLI workflows and other automated
checks that need to verify the API independently. The health endpoint is:

```text
http://127.0.0.1:8765/health
```

All API routes except `/health` require
`Authorization: Bearer <AUTOMATA_API_TOKEN>`, and WebSocket clients must send
the same token in an `authenticate` first frame. The API only accepts loopback
bind addresses. Override `AUTOMATA_API_PORT` when running multiple isolated
checks.

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

The same group contains the backend lint and type-check tools:

```bash
uv run --directory api --group dev --locked ruff check automata_api tests
uv run --directory api --group dev --locked pyright
```

## Observability

The backend writes low-overhead diagnostic JSONL and a separate span index under
`AUTOMATA_DATA_DIR/observability` (or `api/.data/observability` in local
development):

```text
observability/
  observability.db
  logs/automata-*.jsonl
  profiles/<profile-session-id>/
    manifest.json
    events-*.jsonl
    samples-*.jsonl
    content-*.jsonl
```

Normal diagnostic and profile records contain timing, sizes, hashes, model/tool
names, statuses, usage metadata, and correlation IDs, but not prompt, response,
tool argument, or tool output bodies. `content-*.jsonl` is created only by the
explicit `-ProfileCaptureContent` mode and should be treated as sensitive even
after best-effort redaction.

The span index is intentionally separate from `automata.db` and
`agent_run_events`: observability write failures degrade to JSONL/stderr and do
not block agent runs or WebSocket replay. See
[`../Docs/agent-observability-collection.md`](../Docs/agent-observability-collection.md)
for the record contract, retention defaults, and instrumentation boundaries.

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

Sessions accept a persisted permission preset at creation time and through
`PATCH /sessions/{session_id}`:

```json
{
  "title": "Local automation",
  "working_directory": "D:/workspace/project",
  "permission_preset": "full_access"
}
```

`permission_preset` defaults to `default`. In `default`, write, command,
destructive, and policy-defined external actions continue through the approval
broker. In `full_access`, decisions that would normally prompt are executed
without approval. The selected preset is copied to each Run when the Run is
created, so changing a Session does not alter an already active Run.

Full Access does not override explicit policy denials, MCP connection/grant
requirements, hidden/deferred tool routing, or the Plan-mode write prohibition.
It also does not enable a sandbox: commands still run with the API process's OS
permissions. Structured file tools remain confined to the Session workspace.

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
is running, emits provider-driven `token` events as text is generated, then
finishes with `done`.
Tool call and result events are also saved as visible `tool` messages, so
reopening a session preserves the run activity shown during streaming.
`context_compressed` includes `scope` (`history` or `loop`), before/after
character counts, summary size, and compressed message counts. The built-in
tools are real `read_file`, `write_file`, `rg`, `grep`, `exec_command`,
`run_bash`, `apply_patch`, and `apply_patch_preview` tools. File tools read and
write UTF-8 text within the workspace only. For search, the agent should prefer
`rg`; the `rg` tool falls back to `grep`, then to `run_bash` with a suitable search
command when native search commands are unavailable. `exec_command` executes
inside the workspace, supports `shell=bash` and `shell=powershell`, caps
timeouts at 120 seconds, caps command output, and returns stdout, stderr,
combined output, exit code, timeout, duration, shell, and truncation metadata.
`run_bash` remains available as a compatibility bash-only command tool.

The same `rg` tool supports bounded, read-only file enumeration without a shell
or command approval:

```json
{
  "mode": "files",
  "path": ".",
  "include_globs": ["*.py"],
  "exclude_globs": ["api/.venv/**"],
  "hidden": false,
  "max_depth": 6,
  "limit": 500
}
```

Files mode returns a compact sorted `files` array and never accepts raw ripgrep
arguments. It defaults to 500 paths, caps the result at 2,000 paths and 20,000
characters, does not follow symbolic links, and reports truncation so the agent
can narrow `path` or `include_globs`. It uses `rg --files` first, then a safe
Git listing, and finally a non-symlink-following filesystem walk whose degraded
ignore semantics are explicit in the result. Calls without `mode` retain the
existing text-search behavior. See the implemented
[`rg files mode design`](../Docs/Archived/rg-files-mode-design.md) for the
protocol, safety boundary, fallback semantics, and test matrix.

`apply_patch` and `apply_patch_preview` use Codex-style patches by default:

```text
*** Begin Patch
*** Update File: path/to/file.py
@@
 context line
-old line
+new line
*** End Patch
```

Use `*** Add File: path`, `*** Update File: path`, and
`*** Delete File: path` sections. Update hunks use `@@` without line numbers
and must include enough context to match uniquely. The backend still accepts
unified diff patches as a compatibility path, but agents should prefer
Codex-style patches.

Plan mode is enforced by the backend, not only by prompting. It exposes only
`read_file`, `rg`, `grep`, and `apply_patch_preview`. Requests for `run_bash`,
`exec_command`, `write_file`, or `apply_patch` return a failed tool result with
`blocked_by_plan_mode` and do not execute the tool.
