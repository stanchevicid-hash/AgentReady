"""Configuration: environment variables, optional .env file, model roles."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | None = None) -> None:
    """Minimal .env loader (KEY=VALUE lines). Existing env vars win."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# The three Nemotron tiers and the job each one does in AgentReady.
# Nano  -> high-volume, cheap calls (failure triage, classification)
# Super -> the coding agent under test + task writing (tool use, multi-step)
# Ultra -> deep reasoning (root-cause synthesis, writing the fix)
ROLE_DESCRIPTIONS = {
    "nano": "Fast triage: classifies every failed agent run into a failure category",
    "super": "Coding agent under test + writes realistic issue reports for each task",
    "ultra": "Root-cause synthesis across all runs + writes the improved AGENTS.md",
}

DEFAULT_MODELS = {
    # Exact IDs differ between Token Factory regions/releases. If a model below is
    # not served, AgentReady auto-discovers the closest Nemotron model via /v1/models.
    "nano": "nvidia/nemotron-3-nano-30b-a3b",
    "super": "nvidia/nemotron-3-super-120b-a12b",
    "ultra": "nvidia/nemotron-3-ultra",
}


@dataclass
class Settings:
    api_key: str = ""
    base_url: str = "https://api.tokenfactory.nebius.com/v1"
    tavily_key: str = ""
    models: dict = field(default_factory=dict)
    prices: dict = field(default_factory=dict)  # role -> (input $/1M, output $/1M)
    runs_dir: Path = ROOT / "runs"
    executor: str = "local"
    nebius_image: str = ""
    nebius_platform: str = "cpu-e1"
    nebius_preset: str = "2vcpu-8gb"
    extra_body: dict = field(default_factory=dict)  # merged into every chat request

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        models = {
            role: os.environ.get(f"AGENTREADY_MODEL_{role.upper()}", default)
            for role, default in DEFAULT_MODELS.items()
        }
        prices = {}
        for role in DEFAULT_MODELS:
            raw = os.environ.get(f"AGENTREADY_PRICE_{role.upper()}", "")
            if raw:
                try:
                    pin, pout = (float(x) for x in raw.split(","))
                    prices[role] = (pin, pout)
                except ValueError:
                    pass
        extra = {}
        raw_extra = os.environ.get("AGENTREADY_EXTRA_BODY", "").strip()
        if raw_extra:
            try:
                extra = json.loads(raw_extra)
            except json.JSONDecodeError:
                print("warning: AGENTREADY_EXTRA_BODY is not valid JSON, ignoring it")
        return cls(
            extra_body=extra if isinstance(extra, dict) else {},
            api_key=os.environ.get("NEBIUS_API_KEY", ""),
            base_url=os.environ.get("NEBIUS_BASE_URL", cls.base_url).rstrip("/"),
            tavily_key=os.environ.get("TAVILY_API_KEY", ""),
            models=models,
            prices=prices,
            runs_dir=Path(os.environ.get("AGENTREADY_RUNS_DIR", str(ROOT / "runs"))),
            executor=os.environ.get("AGENTREADY_EXECUTOR", "local"),
            nebius_image=os.environ.get("AGENTREADY_NEBIUS_IMAGE", ""),
            nebius_platform=os.environ.get("AGENTREADY_NEBIUS_PLATFORM", "cpu-e1"),
            nebius_preset=os.environ.get("AGENTREADY_NEBIUS_PRESET", "2vcpu-8gb"),
        )
