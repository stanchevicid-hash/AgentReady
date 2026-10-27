"""Where agent commands execute.

local  - subprocess in a throwaway copy of the repo, secrets stripped, timeouts.
docker - every command in a fresh `python:3.12-slim` container, no network.
(The whole episode can also run remotely on Nebius Serverless Jobs, see jobs.py.)
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from .repo import base_env, run_cmd

# Cheap guard against obviously destructive commands. Real isolation comes from
# the throwaway workspace, the docker runner, or a Nebius Serverless Job.
_DENY = [
    r"\brm\s+-[a-z]*r[a-z]*f?\s+(/|~|\$HOME|\.\.)", r"\bmkfs\b", r"\bshutdown\b", r"\breboot\b",
    r":\(\)\s*\{", r"\bdd\s+if=", r"\bformat\s+[a-z]:", r"\bdel\s+/[sq]", r"\bcurl\b.*\|\s*(sh|bash)",
    r"\bgit\s+push\b", r"\bpip\s+install\b",
]


def denied(cmd: str) -> str | None:
    for pat in _DENY:
        if re.search(pat, cmd, re.IGNORECASE):
            return pat
    return None


class LocalRunner:
    name = "local"

    def __init__(self, shim: Path, extra_env: dict | None = None, timeout: int = 60):
        self.env = base_env(shim, extra_env)
        self.timeout = timeout
        self.shell = shell_description()

    def run(self, cmd: str, cwd: Path) -> tuple[int, str]:
        bad = denied(cmd)
        if bad:
            return 126, f"[blocked by AgentReady sandbox policy: matches {bad!r}]"
        return run_cmd(cmd, cwd, self.env, timeout=self.timeout)


class DockerRunner:
    name = "docker"

    def __init__(self, image: str = "python:3.12-slim", extra_env: dict | None = None, timeout: int = 90):
        if not shutil.which("docker"):
            raise RuntimeError("docker is not installed; use --executor local")
        self.image = image
        self.extra_env = extra_env or {}
        self.timeout = timeout
        self.shell = "Linux container (python:3.12-slim), commands run in /bin/sh, no network"

    def run(self, cmd: str, cwd: Path) -> tuple[int, str]:
        bad = denied(cmd)
        if bad:
            return 126, f"[blocked by AgentReady sandbox policy: matches {bad!r}]"
        args = [
            "docker", "run", "--rm", "--network", "none", "--memory", "1g", "--cpus", "1",
            "-v", f"{cwd.resolve()}:/work", "-w", "/work",
        ]
        for k, v in self.extra_env.items():
            args += ["-e", f"{k}={v}"]
        args += [self.image, "sh", "-c", cmd]
        try:
            proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=self.timeout)
            return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            return 124, f"[timed out after {self.timeout}s]"


def shell_description() -> str:
    if os.name == "nt":
        return "Windows, commands run in cmd.exe (use `set VAR=value&& command` for env vars)"
    return "Linux/macOS, commands run in /bin/sh"
