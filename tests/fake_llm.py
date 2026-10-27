"""A scripted stand-in for Token Factory, used ONLY by the offline test-suite to
exercise the pipeline plumbing. The real product always calls Nemotron models."""

from __future__ import annotations

import json
import re
from pathlib import Path

from agentready.llm import UsageTracker


class ScriptedLLM:
    def __init__(self, pristine_root: Path):
        self.root = Path(pristine_root)
        self.usage = UsageTracker()
        self.models = {"nano": "fake-nano", "super": "fake-super", "ultra": "fake-ultra"}

    def _find_symbol_file(self, symbol: str) -> str:
        for p in sorted(self.root.rglob("*.py")):
            if "tests" in p.parts:
                continue
            if re.search(rf"def {re.escape(symbol)}\(", p.read_text(encoding="utf-8")):
                return p.relative_to(self.root).as_posix()
        raise AssertionError(symbol)

    def chat_with_usage(self, role, messages, max_tokens=2048, temperature=0.2, retries=3):
        text = self._reply(role, messages)
        self.usage.add(role, 100, 20, 0.01)
        return text, 100, 20

    def chat(self, role, messages, max_tokens=2048, temperature=0.2, retries=3):
        return self.chat_with_usage(role, messages)[0]

    def chat_json(self, role, messages, max_tokens=2048):
        return json.loads(self.chat(role, messages))

    def _reply(self, role, messages):
        first = messages[0]["content"]
        last = messages[-1]["content"]
        if role == "super" and "writing a GitHub issue" in first:
            sym = re.search(r"Function involved: (\w+)", first).group(1)
            return f"# `{sym}` gives wrong results\n\nWhen I call `{sym}` the result is wrong. Expected the documented behaviour."
        if role == "super":
            sym = re.search(r"`(\w+)`", messages[1]["content"]).group(1)
            path = self._find_symbol_file(sym)
            n_assist = sum(1 for m in messages if m["role"] == "assistant")
            improved = "Project instructions for agents" in first
            plan = [
                {"action": "list_dir", "args": {"path": "."}},
                {"action": "run", "args": {"command": "pytest"}},
                {"action": "search", "args": {"pattern": f"def {sym}"}},
                {"action": "read_file", "args": {"path": path}},
                {"action": "write_file", "args": {"path": path, "content": (self.root / path).read_text(encoding="utf-8")}},
                {"action": "finish", "args": {"summary": "restored behaviour"}},
            ]
            if improved:
                plan = plan[2:]
            step = plan[min(n_assist, len(plan) - 1)]
            return json.dumps({"thought": "scripted", **step})
        if role == "nano":
            return json.dumps({"category": "test_discovery", "secondary": "misleading_docs",
                               "explanation": "README says pytest, which is not installed.",
                               "evidence_steps": [2], "missing_knowledge": "Tests run with unittest and PYTHONPATH=src"})
        if role == "ultra" and "Return ONLY JSON" in first:
            return json.dumps({"summary": "Docs are stale.", "findings": [
                {"title": "README test command is wrong", "category": "misleading_docs", "severity": "high",
                 "affected_runs": 3, "evidence": "runs ran pytest", "fix": "Document unittest + PYTHONPATH"}]})
        if role == "ultra":
            return "# AGENTS.md\n\n## Running tests\n\nUse unittest with PYTHONPATH=src. " + "x" * 120
        raise AssertionError(f"unexpected call {role}: {first[:80]}")
