# Automata Agent UI

Minimal desktop shell for the local coding agent. It uses Tauri 2, React,
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

The React UI is compiled into the Tauri desktop executable. The FastAPI
streaming backend is bundled as a Tauri sidecar and started by the desktop
process.

## Embedded files

The conversation keeps its own title, message area and composer. Its top-right
control opens or folds an independent file work panel beside the conversation.
All file tabs and text reading stay inside that panel. The panel has a separate
control to fold its internal directory tree, expanding only the reader without
changing the conversation layout. The information button opens workspace
metadata and diagnostics inside the panel.

Opening a file adds a tab above the panel's reader. Tabs support switching,
closing, arrow-key navigation and Delete; **打开文件** returns to the file picker.
Each file retains its reading position and wrap setting until closed, including
when the entire panel is folded. The directory tree loads folders on expansion;
the filter matches files in expanded directories and keeps folders available to
explore. Breadcrumbs reveal the corresponding directory. The reader supports
line numbers, wrapping and a per-file refresh. On compact screens the work panel
opens separately over the chat; choosing a file folds only its directory drawer.

Previews are read-only, support UTF-8, BOM-marked UTF-16/32 and GB18030, and show
an explicit notice when limited to 256 KiB or 5000 lines. Binary or unreadable
files report a readable error. Directory listings are limited to 2000 entries.
Switching sessions or workspaces resets tabs and navigation and cancels pending
requests. When an agent run stops streaming, expanded directories and the active
preview refresh; inactive file previews reload when next selected.
