@echo off
REM Double-click to start the PySearch frontend (Vite dev server on http://localhost:5173).
cd /d "%~dp0..\frontend"
if not exist node_modules (
  echo Installing frontend dependencies...
  call npm install
)
call npm run dev
