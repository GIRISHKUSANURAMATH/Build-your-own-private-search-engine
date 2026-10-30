@echo off
REM Double-click launcher for restart-backend.ps1 (stops the old server on port 8000,
REM runs the checks and tests, then starts uvicorn with auto-reload).
powershell -NoProfile -NoExit -File "%~dp0restart-backend.ps1"
