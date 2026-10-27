# One image, two jobs:
#   - Nebius Serverless Endpoint: serves the AgentReady dashboard (default CMD)
#   - Nebius Serverless Job: runs one agent episode
#       (container command: python -m agentready.worker --spec /spec.json)
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pytest

WORKDIR /app
COPY agentready ./agentready
COPY web ./web
COPY examples ./examples

ENV PYTHONUNBUFFERED=1 \
    AGENTREADY_RUNS_DIR=/data/runs

EXPOSE 8080
CMD ["python", "-m", "agentready", "serve", "--host", "0.0.0.0", "--port", "8080"]
