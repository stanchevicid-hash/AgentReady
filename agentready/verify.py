"""AI-code verifier: apply a patch an AI wrote to a throwaway copy of the repo and check it for real.

Every check runs against the actual code (git apply, Python's ast, the repo's own test suite);
the optional Nemotron review is clearly labelled as the model's opinion.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .repo import base_env, detect_test_command, fetch_repo, is_test_file, make_workspace, new_temp_root, \
    python_shim_dir, run_cmd

SECRET_RE = re.compile(r"""(api[_-]?key|secret|token|passw(or)?d)\s*[:=]\s*['"][^'"\s]{8,}['"]""", re.I)
RISKY_CALLS = {"eval", "exec", "compile", "__import__"}
RISKY_ATTRS = {("pickle", "loads"), ("pickle", "load"), ("os", "system"), ("yaml", "load"), ("marshal", "loads")}

REVIEW_PROMPT = """You review a code change that an AI coding assistant wrote. Be strict and concrete.
Only report real problems you can point to in the diff: bugs, missing edge cases, security issues,
behaviour changes the change did not intend, tests that were weakened. Do not invent problems.

Automated checks already ran:
{checks}

Diff:
```diff
{diff}
```

Report at most 6 issues, most important first. Reply with ONLY this JSON:
{{"verdict": "looks_good" | "needs_changes" | "reject",
  "summary": "one sentence",
  "issues": [{{"severity": "high"|"medium"|"low", "title": "...", "detail": "...", "file": "..."}}]}}"""


def _check(key: str, status: str, detail: str = "", items: list | None = None) -> dict:
    return {"key": key, "status": status, "detail": detail, "items": items or []}


def changed_files(patch: str) -> list[str]:
    files = []
    for m in re.finditer(r"^\+\+\+ (?:b/)?(\S+)", patch, re.M):
        if m.group(1) != "/dev/null" and m.group(1) not in files:
            files.append(m.group(1))
    return files


def added_lines(patch: str) -> list[tuple[str, str]]:
    out, cur = [], None
    for line in patch.splitlines():
        if line.startswith("+++ "):
            cur = re.sub(r"^(b/)", "", line[4:].strip())
        elif line.startswith("+") and not line.startswith("+++") and cur:
            out.append((cur, line[1:]))
    return out


def risky_code(root: Path, files: list[str], patch: str) -> list[str]:
    found = []
    added = {(f, l.strip()) for f, l in added_lines(patch)}
    for rel in files:
        p = root / rel
        if not rel.endswith(".py") or not p.exists():
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src)
        except SyntaxError:
            continue
        lines = src.splitlines()
        for node in ast.walk(tree):
            what = None
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name) and f.id in RISKY_CALLS:
                    what = f"{f.id}()"
                elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and (f.value.id, f.attr) in RISKY_ATTRS:
                    what = f"{f.value.id}.{f.attr}()"
                elif any(k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True
                         for k in node.keywords):
                    what = "shell=True"
            elif isinstance(node, ast.ExceptHandler) and node.type is None:
                what = "bare except:"
            # only report what this patch added, not pre-existing code
            if what and (rel, lines[node.lineno - 1].strip()) in added:
                found.append(f"{rel}:{node.lineno}  {what}")
    for f, l in added_lines(patch):
        if SECRET_RE.search(l):
            found.append(f"{f}  possible hard-coded secret")
    return found


def unknown_imports(root: Path, files: list[str], patch: str) -> list[str]:
    """Top-level imports the patch added that are not stdlib, not in the repo and not installed."""
    local = {p.name for p in root.iterdir() if p.is_dir()} | {p.stem for p in root.glob("*.py")}
    for src_dir in ("src", "lib"):
        if (root / src_dir).is_dir():
            local |= {p.name for p in (root / src_dir).iterdir()} | {p.stem for p in (root / src_dir).glob("*.py")}
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    out = []
    for f, l in added_lines(patch):
        m = re.match(r"\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", l)
        if not m or not f.endswith(".py"):
            continue
        name = (m.group(1) or m.group(2)).split(".")[0]
        if not name or name in stdlib or name in local or name in out:
            continue
        if importlib.util.find_spec(name) is None:
            out.append(name)
    return out


def _tests(root: Path, shim: Path, info: dict) -> tuple[bool, str]:
    rc, out = run_cmd(info["cmd"], root, base_env(shim, info.get("env")), timeout=300)
    return rc == 0, out[-3000:]


def verify(source: str, patch: str, llm=None, test_cmd: str | None = None, on_check=None) -> dict:
    work = new_temp_root("agentready-verify-")
    checks: list[dict] = []

    def add(c):
        checks.append(c)
        if on_check:
            on_check(c)

    try:
        pristine = fetch_repo(source, work)
        shim = python_shim_dir(work)
        patched = make_workspace(pristine, work, "patched")
        if not patch.endswith("\n"):
            patch += "\n"
        pfile = work / "change.patch"
        pfile.write_bytes(patch.encode("utf-8"))  # bytes: write_text would turn \n into \r\n on Windows
        files = changed_files(patch)

        # 1. does the patch apply?
        proc = subprocess.run(["git", "apply", "--whitespace=nowarn", "--ignore-whitespace", "--recount", str(pfile)],
                              cwd=str(patched), capture_output=True, text=True)
        if proc.returncode != 0 or not files:
            add(_check("applies", "fail", (proc.stderr or "no file changes found in the patch").strip()[:600], files))
            return {"checks": checks, "files": files}
        add(_check("applies", "pass", f"{len(files)} file(s)", files))

        # 2. syntax
        bad = []
        for rel in files:
            p = patched / rel
            if rel.endswith(".py") and p.exists():
                try:
                    ast.parse(p.read_text(encoding="utf-8", errors="replace"))
                except SyntaxError as exc:
                    bad.append(f"{rel}:{exc.lineno}  {exc.msg}")
        add(_check("syntax", "fail" if bad else "pass", "", bad))

        # 3. did the AI touch tests?
        tests = [f for f in files if is_test_file(f)]
        add(_check("tests_edited", "warn" if tests else "pass", "", tests))

        # 4. risky code + 5. invented packages
        risky = risky_code(patched, files, patch)
        add(_check("risky", "warn" if risky else "pass", "", risky))
        unknown = unknown_imports(patched, files, patch)
        add(_check("imports", "fail" if unknown else "pass", "", unknown))

        # 6. the repo's own tests, before and after
        try:
            info = detect_test_command(pristine, shim, test_cmd)
        except Exception as exc:
            add(_check("tests", "skip", str(exc)[:400]))
            info = None
        if info:
            ok_before, _ = _tests(pristine, shim, info)
            ok_after, out_after = _tests(patched, shim, info)
            status = "pass" if ok_after else ("fail" if ok_before else "warn")
            add(_check("tests", status, f"{info['cmd']}  before: {'pass' if ok_before else 'fail'}, "
                                        f"after: {'pass' if ok_after else 'fail'}", [out_after[-1500:]]))

        # 7. Nemotron review (opinion, only when a key is configured)
        if llm is not None:
            diff = patch[:12000]
            summary = "\n".join(f"- {c['key']}: {c['status']} {c['detail']}" for c in checks)
            try:
                review = _review(llm, REVIEW_PROMPT.format(checks=summary, diff=diff))
                verdict = review.get("verdict", "needs_changes")
                status = {"looks_good": "pass", "reject": "fail"}.get(verdict, "warn")
                add({**_check("ai_review", status, str(review.get("summary", ""))[:400]),
                     "issues": [i for i in review.get("issues", []) if isinstance(i, dict)][:12],
                     "model": llm.models.get("super", "")})
            except Exception as exc:
                add(_check("ai_review", "skip", f"review unavailable: {str(exc)[:200]}"))
        else:
            add(_check("ai_review", "skip", "NEBIUS_API_KEY not set"))
        return {"checks": checks, "files": files}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _review(llm, prompt: str) -> dict:
    # A reply cut off by the token limit can still contain a complete inner {...} (one issue), which
    # extract_json would happily return. Only accept the top-level object, and retry once with more room.
    for max_tokens in (2500, 6000):
        try:
            out = llm.chat_json("super", [{"role": "user", "content": prompt}], max_tokens=max_tokens)
        except Exception:
            continue
        if isinstance(out, dict) and "verdict" in out:
            return out
    raise ValueError("the model did not return a complete review")


def score(checks: list[dict]) -> int:
    """0-100 from the checks that ran: pass = full, warn = half, fail = zero. Skipped checks don't count."""
    weights = {"applies": 3, "syntax": 3, "tests": 4, "imports": 2, "tests_edited": 2, "risky": 2, "ai_review": 2}
    got = total = 0.0
    for c in checks:
        if c["status"] == "skip":
            continue
        w = weights.get(c["key"], 1)
        total += w
        got += w * {"pass": 1, "warn": 0.5}.get(c["status"], 0)
    if any(c["key"] == "applies" and c["status"] == "fail" for c in checks):
        return 0
    return round(100 * got / total) if total else 0


def new_id() -> str:
    return time.strftime("v%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
