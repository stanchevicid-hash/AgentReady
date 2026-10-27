@echo off
rem AgentReady for your phone: serves the app on this Wi-Fi network.
rem Open Settings in the app on this PC and scan the QR code with your phone.
cd /d "%~dp0"
set PY=
py -3 --version >nul 2>nul && set PY=py -3
if not defined PY python --version >nul 2>nul && set PY=python
if not defined PY (
  echo Python was not found. Install Python 3.9+ from https://www.python.org/downloads/
  pause
  exit /b 1
)
if not exist .env copy .env.example .env >nul
%PY% -m agentready serve --host 0.0.0.0 --open
pause
