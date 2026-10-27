@echo off
rem AgentReady launcher for Windows: creates .env on first run, checks the key, opens the dashboard.
cd /d "%~dp0"
rem Prefer the py launcher: "python" is often the Microsoft Store stub, which "where" finds but cannot run.
set PY=
py -3 --version >nul 2>nul && set PY=py -3
if not defined PY python --version >nul 2>nul && set PY=python
if not defined PY (
  echo Python was not found. Install Python 3.9+ from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause
  exit /b 1
)
if not exist .env (
  copy .env.example .env >nul
  echo Created .env - put your NEBIUS_API_KEY in it, then run start.bat again.
  notepad .env
  exit /b 0
)
%PY% -m agentready doctor
%PY% -m agentready serve --open
pause
