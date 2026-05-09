# Automata Agent UI Demo

Minimal desktop demo for the local coding agent shell. It uses Tauri 2, React,
TypeScript, Vite, and a small Rust command to prove the desktop bridge is wired.

## Development

```bash
npm install
npm run tauri dev
```

The Python backend can later be bundled as a Tauri sidecar and connected over
WebSocket or SSE.
