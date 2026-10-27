"""The coding agent under test: a compact tool-using agent driven by Nemotron Super.

It deliberately behaves like the coding agents developers use today: it gets the
repository's agent instructions (AGENTS.md and friends) up front, an issue to
solve, and file/search/shell tools. What it does with them is what we measure.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .llm import LLMError, extract_json
from .repo import IGNORE_DIRS, iter_files

MAX_OBS = 3500

SYSTEM_PROMPT = """You are an autonomous software engineering agent working inside a code repository.
Your job: resolve the issue below by editing the code, then verify your fix.

Environment: {shell}. The current directory is the repository root.

You act by replying with EXACTLY ONE JSON object per turn, no prose around it:
{{"thought": "<one or two sentences>", "action": "<tool>", "args": {{...}}}}

Tools:
- list_dir     {{"path": "."}}                                   list a directory
- read_file    {{"path": "x.py", "start": 1, "end": 200}}        read lines (1-based, inclusive)
- search       {{"pattern": "regex", "path": "."}}               grep the codebase
- replace      {{"path": "x.py", "old": "exact text", "new": "replacement"}}  edit one exact occurrence
- write_file   {{"path": "x.py", "content": "full file text"}}   create/overwrite a file
- run          {{"command": "shell command"}}                    run a command (60s timeout)
- finish       {{"summary": "what you changed and how you verified it"}}

Rules: work in small steps, read before you edit, do not modify tests to make them pass,
and run the project's tests before you finish.
{instructions}"""

INSTRUCTIONS_BLOCK = """
=== Project instructions for agents (from {name}) ===
{text}
=== end of project instructions ==="""


@dataclass
class Step:
    n: int
    thought: str
    action: str
    args: dict
    observation: str
    ok: bool = True

    def to_dict(self) -> dict:
        args = dict(self.args)
        for k in ("content", "new", "old"):
            if k in args and isinstance(args[k], str) and len(args[k]) > 600:
                args[k] = args[k][:600] + f"... [{len(args[k])} chars]"
        return {
            "n": self.n, "thought": self.thought[:400], "action": self.action, "args": args,
            "observation": self.observation[:1500], "ok": self.ok,
        }


@dataclass
class Episode:
    task_id: str
    condition: str          # "baseline" | "improved"
    repeat: int
    steps: list = field(default_factory=list)
    finished: bool = False
    summary: str = ""
    error: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    success: bool = False
    tests_touched: list = field(default_factory=list)
    grade_output: str = ""
    first_touch_step: int | None = None   # first step that opened/edited the defective file
    ran_tests: bool = False

    @property
    def key(self) -> str:
        return f"{self.condition}:{self.task_id}:{self.repeat}"

    def to_dict(self) -> dict:
        return {
            "key": self.key, "task_id": self.task_id, "condition": self.condition, "repeat": self.repeat,
            "steps": [s.to_dict() for s in self.steps], "n_steps": len(self.steps),
            "finished": self.finished, "summary": self.summary[:800], "error": self.error[:500],
            "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
            "seconds": round(self.seconds, 1), "success": self.success,
            "tests_touched": self.tests_touched, "grade_output": self.grade_output[-1500:],
            "first_touch_step": self.first_touch_step, "ran_tests": self.ran_tests,
        }


def _safe_path(root: Path, rel: str) -> Path:
    p = (root / (rel or ".")).resolve()
    if p != root.resolve() and root.resolve() not in p.parents:
        raise ValueError(f"path escapes the repository: {rel}")
    return p


def _clip(text: str, limit: int = MAX_OBS) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 3]
    tail = text[-(2 * limit // 3):]
    return f"{head}\n... [{len(text) - limit} chars omitted] ...\n{tail}"


class Tools:
    def __init__(self, root: Path, runner):
        self.root = root
        self.runner = runner

    def list_dir(self, path: str = ".") -> str:
        p = _safe_path(self.root, path)
        if not p.is_dir():
            return f"error: not a directory: {path}"
        entries = []
        for child in sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name)):
            if child.name in IGNORE_DIRS or child.name == ".shim":
                continue
            entries.append(child.name + ("/" if child.is_dir() else ""))
        return "\n".join(entries) or "(empty)"

    def read_file(self, path: str, start: int = 1, end: int = 200) -> str:
        p = _safe_path(self.root, path)
        if not p.is_file():
            return f"error: file not found: {path}"
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(start or 1))
        end = min(len(lines), int(end or start + 199), start + 399)
        body = "\n".join(f"{i:>4}| {lines[i - 1]}" for i in range(start, end + 1))
        return f"{path} (lines {start}-{end} of {len(lines)})\n{body}"

    def search(self, pattern: str, path: str = ".") -> str:
        base = _safe_path(self.root, path)
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            return f"error: bad regex: {exc}"
        hits = []
        files = [base] if base.is_file() else iter_files(base)
        for f in files:
            if f.suffix in (".pyc", ".png", ".jpg", ".gif", ".zip", ".lock") or ".shim" in f.parts:
                continue
            try:
                for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{f.relative_to(self.root).as_posix()}:{i}: {line.strip()[:160]}")
                        if len(hits) >= 60:
                            return "\n".join(hits) + "\n... (more matches truncated)"
            except OSError:
                continue
        return "\n".join(hits) or "no matches"

    def replace(self, path: str, old: str, new: str) -> str:
        p = _safe_path(self.root, path)
        if not p.is_file():
            return f"error: file not found: {path}"
        text = p.read_text(encoding="utf-8", errors="replace")
        count = text.count(old) if old else 0
        if count != 1:
            return f"error: 'old' text found {count} times in {path}; it must match exactly once"
        p.write_text(text.replace(old, new, 1), encoding="utf-8")
        return f"replaced 1 occurrence in {path}"

    def write_file(self, path: str, content: str) -> str:
        p = _safe_path(self.root, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {path}"

    def run(self, command: str) -> str:
        rc, out = self.runner.run(command, self.root)
        return f"exit code {rc}\n{_clip(out)}"


STEP_TOKENS = 4000
TEST_CMD_HINT = re.compile(r"\b(pytest|unittest|tox|nox|make test|npm test|go test|cargo test)\b")


def run_episode(llm, task, workspace: Path, runner, instructions: dict, condition: str, repeat: int,
                max_steps: int = 24, on_step=None) -> Episode:
    ep = Episode(task_id=task.id, condition=condition, repeat=repeat)
    tools = Tools(workspace, runner)
    instr = "".join(INSTRUCTIONS_BLOCK.format(name=n, text=t.strip()) for n, t in instructions.items())
    system = SYSTEM_PROMPT.format(shell=runner.shell, instructions=instr)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Issue to resolve:\n\n{task.issue}\n\nRepository root:\n{tools.list_dir('.')}"},
    ]
    started = time.time()
    consecutive_bad = 0
    for n in range(1, max_steps + 1):
        try:
            # room for a write_file of a whole module; a cut-off reply is a harness failure, not the repo's
            reply, pt, ct = llm.chat_with_usage("super", messages, max_tokens=STEP_TOKENS, temperature=0.2)
        except LLMError as exc:
            ep.error = str(exc)
            break
        ep.prompt_tokens += pt
        ep.completion_tokens += ct
        try:
            act = extract_json(reply)
            if not isinstance(act, dict) or "action" not in act:
                raise ValueError("missing action")
        except Exception:
            consecutive_bad += 1
            cut = ct >= STEP_TOKENS * 0.95
            step = Step(n, "", "invalid", {}, "Your reply was cut off because it was too long. Use a short "
                        "`replace` edit instead of rewriting a whole file." if cut else
                        "Your reply was not a single valid JSON action.", ok=False)
            ep.steps.append(step)
            messages += [{"role": "assistant", "content": reply[:2000]},
                         {"role": "user", "content": step.observation + " Reply with ONE JSON object."}]
            if on_step:
                on_step(ep, step)
            if consecutive_bad >= 3:
                ep.error = "agent produced invalid actions 3 times in a row"
                break
            continue
        consecutive_bad = 0
        action = str(act.get("action", "")).strip()
        args = act.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        thought = str(act.get("thought", ""))
        ok = True
        if action == "finish":
            ep.finished = True
            ep.summary = str(args.get("summary", ""))
            step = Step(n, thought, action, args, "finished")
            ep.steps.append(step)
            if on_step:
                on_step(ep, step)
            break
        fn = getattr(tools, action, None) if action in ("list_dir", "read_file", "search", "replace", "write_file", "run") else None
        if fn is None:
            obs, ok = f"error: unknown tool '{action}'", False
        else:
            try:
                obs = fn(**{k: v for k, v in args.items() if k in fn.__code__.co_varnames})
                ok = not obs.startswith("error")
            except TypeError as exc:
                obs, ok = f"error: bad arguments for {action}: {exc}", False
            except Exception as exc:
                obs, ok = f"error: {exc}", False
        if action == "run" and TEST_CMD_HINT.search(str(args.get("command", ""))):
            ep.ran_tests = True
        touched = re.sub(r"^(\./)+", "", str(args.get("path", "")).replace("\\", "/"))
        if ep.first_touch_step is None and action in ("read_file", "replace", "write_file") and touched == task.file:
            ep.first_touch_step = n
        step = Step(n, thought, action, args, obs, ok)
        ep.steps.append(step)
        if on_step:
            on_step(ep, step)
        messages += [
            {"role": "assistant", "content": json.dumps(act)[:4000]},
            {"role": "user", "content": f"Observation:\n{_clip(obs)}"},
        ]
        # Keep context bounded: compress old observations.
        if len(messages) > 30:
            for m in messages[2:-12]:
                if m["role"] == "user" and len(m["content"]) > 400:
                    m["content"] = m["content"][:400] + " ... [older observation truncated]"
    ep.seconds = time.time() - started
    return ep
