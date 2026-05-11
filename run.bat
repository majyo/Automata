@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
pwsh -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%run.ps1" %*

if errorlevel 1 (
  echo.
  echo Automata failed to start. Press any key to close this window.
  pause >nul
)
