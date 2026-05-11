# Automata Agent UI Demo

Minimal desktop demo for the local coding agent shell. It uses Tauri 2, React,
TypeScript, Vite, and a small Rust command to prove the desktop bridge is wired.

## Desktop App

From the repository root, use the one-click runner to build the Python API
sidecar, compile the Tauri desktop app, and launch the release executable:

```powershell
.\run.ps1
```

You can also double-click `run.bat` on Windows.

For development mode:

```powershell
.\run.ps1 -Mode dev
```

To compile without launching:

```powershell
.\run.ps1 -Mode build
```

The React UI is compiled into the Tauri desktop executable. The fake FastAPI
streaming backend is bundled as a Tauri sidecar and started by the desktop
process.
