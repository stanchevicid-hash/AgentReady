"""Run agent episodes as Nebius Serverless AI Jobs.

Each (task, condition, repeat) episode becomes one isolated job: a fresh container
on Nebius AI Cloud clones the repo, injects the defect, runs the Nemotron agent
against Token Factory, grades it and prints the result to its logs. AgentReady
fans out dozens of these in parallel and collects the results.

Requires the `nebius` CLI (`nebius auth login`) and a worker image built from
the Dockerfile in this repo and pushed to a registry the job can pull from.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time

from .config import Settings

RESULT_START = "===AGENTREADY_RESULT==="
RESULT_END = "===AGENTREADY_END==="
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "CANCELED", "ERROR", "SUCCEEDED"}


def _find_state(obj):
    if isinstance(obj, dict):
        for k in ("state", "phase", "status"):
            v = obj.get(k)
            if isinstance(v, str) and v.isupper():
                return v
        for v in obj.values():
            s = _find_state(v)
            if s:
                return s
    elif isinstance(obj, list):
        for v in obj:
            s = _find_state(v)
            if s:
                return s
    return None


def _find_id(obj):
    if isinstance(obj, dict):
        meta = obj.get("metadata")
        if isinstance(meta, dict) and meta.get("id"):
            return meta["id"]
        if isinstance(obj.get("id"), str):
            return obj["id"]
    return None


def parse_result(logs: str) -> dict | None:
    m = re.search(re.escape(RESULT_START) + r"\s*(\{.*\})\s*" + re.escape(RESULT_END), logs, re.DOTALL)
    return json.loads(m.group(1)) if m else None


class NebiusJobsRunner:
    remote = True
    name = "nebius"
    shell = "Linux container on Nebius Serverless Jobs, commands run in /bin/sh"

    def __init__(self, settings: Settings, poll_seconds: float = 10, timeout_minutes: int = 30):
        if not shutil.which("nebius"):
            raise RuntimeError("The `nebius` CLI is not installed. See https://docs.nebius.com/cli/install")
        if not settings.nebius_image:
            raise RuntimeError("Set AGENTREADY_NEBIUS_IMAGE to the pushed worker image (see Dockerfile)")
        self.s = settings
        self.poll = poll_seconds
        self.timeout = timeout_minutes

    def _cli(self, *args: str, timeout: int = 120) -> str:
        proc = subprocess.run(["nebius", *args], capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"nebius {' '.join(args[:3])} failed: {proc.stderr.strip()[:400]}")
        return proc.stdout

    def run_episode(self, task, condition, repeat, agents_md, test_info, source, max_steps, models) -> dict:
        if not re.match(r"^(https?://|git@)", source):
            raise RuntimeError("Nebius Jobs executor needs a git URL source (the job clones the repo)")
        spec = {
            "source": source, "task": {**task.public(), "mutated_source": task.mutated_source},
            "condition": condition, "repeat": repeat, "agents_md": agents_md, "test_info": test_info,
            "max_steps": max_steps, "models": models, "base_url": self.s.base_url,
        }
        name = re.sub(r"[^a-z0-9-]", "-", f"agentready-{condition}-{task.id}-{repeat}-{int(time.time())}".lower())[:60]
        fd, spec_path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(spec, fh)
        key_secret = os.environ.get("AGENTREADY_NEBIUS_KEY_SECRET")  # MysteryBox selector, preferred
        env_args = (["--env-secret", f"NEBIUS_API_KEY={key_secret}"] if key_secret
                    else ["--env", f"NEBIUS_API_KEY={self.s.api_key}"])
        try:
            self._cli(
                "ai", "job", "create", "--name", name, "--image", self.s.nebius_image,
                "--container-command", "python", "--args", "-m agentready.worker --spec /spec.json",
                "--inject-file", f"{spec_path}:/spec.json", *env_args,
                "--platform", self.s.nebius_platform, "--preset", self.s.nebius_preset,
                "--timeout", f"{self.timeout}m", timeout=300,
            )
            job_id = _find_id(json.loads(self._cli("ai", "job", "get-by-name", "--name", name, "--format", "json")))
            deadline = time.time() + self.timeout * 60 + 300
            state = None
            while time.time() < deadline:
                state = _find_state(json.loads(self._cli("ai", "job", "get", job_id, "--format", "json")))
                if state in TERMINAL:
                    break
                time.sleep(self.poll)
            logs = self._cli("ai", "job", "logs", job_id, timeout=300)
            result = parse_result(logs)
            if result is None:
                raise RuntimeError(f"job {job_id} ended in state {state} without a result")
            result["job_id"] = job_id
            return result
        finally:
            os.unlink(spec_path)
