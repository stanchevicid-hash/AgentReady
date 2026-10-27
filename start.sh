#!/bin/sh
# AgentReady launcher for macOS/Linux.
cd "$(dirname "$0")" || exit 1
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env - put your NEBIUS_API_KEY in it, then run ./start.sh again."
  exit 0
fi
python3 -m agentready doctor
exec python3 -m agentready serve --open
