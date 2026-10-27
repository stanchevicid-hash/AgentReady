"""AI-code verifier: apply a patch an AI wrote to a throwaway copy of the repo and check it for real.

Every check runs against the actual code (git apply, Python's ast, the repo's own test suite);
the optional Nemotron review is clearly labelled as the model's opinion.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .llm import LLMError
from .repo import base_env, detect_test_command, fetch_repo, is_test_file, make_workspace, new_temp_root, \
    python_shim_dir, run_cmd

SECRET_RE = re.compile(r"""(api[_-]?key|secret|token|passw(or)?d)\s*[:=]\s*['"][^'"\s]{8,}['"]""", re.I)
RISKY_CALLS = {"eval", "exec", "compile", "__import__"}
RISKY_ATTRS = {("pickle", "loads"), ("pickle", "load"), ("os", "system"), ("yaml", "load"), ("marshal", "loads")}
# the same, for code that can't be parsed on its own (a fragment of a diff without its repository)
RISKY_RE = [(re.compile(r"\b(eval|exec|__import__)\s*\("), "{0}()"), (re.compile(r"shell\s*=\s*True"), "shell=True"),
            (re.compile(r"^\s*except\s*:"), "bare except:"), (re.compile(r"\b(pickle|marshal)\.loads?\s*\("), "{0}.load()"),
            (re.compile(r"\bos\.system\s*\("), "os.system()")]
# import name -> PyPI project, where they differ
PYPI_NAME = {"yaml": "PyYAML", "cv2": "opencv-python", "sklearn": "scikit-learn", "bs4": "beautifulsoup4",
             "PIL": "Pillow", "dateutil": "python-dateutil", "dotenv": "python-dotenv", "jwt": "PyJWT",
             "attr": "attrs", "Crypto": "pycryptodome", "magic": "python-magic", "serial": "pyserial"}
SNIPPET = "snippet.py"

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
        src = p.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            for f, line in added_lines(patch):
                if f == rel:
                    for rx, label in RISKY_RE:
                        m = rx.search(line)
                        if m:
                            found.append(f"{rel}  {label.format(*m.groups())}")
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


def pypi_exists(name: str) -> bool | None:
    """Is there a project with this name on PyPI? None when PyPI can't be reached."""
    project = PYPI_NAME.get(name, name)
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{project}/json", timeout=5) as r:
            return r.status == 200
    except urllib.error.HTTPError as exc:
        return False if exc.code == 404 else None
    except (OSError, ValueError):
        return None


def unknown_imports(root: Path, files: list[str], patch: str) -> list[str]:
    """Top-level imports the patch added that are not stdlib, not in the repo and not installed."""
    local = {p.name for p in root.iterdir() if p.is_dir()} | {p.stem for p in root.glob("*.py")}
    # every folder and module the change itself touches counts as the project's own code
    for f in files:
        parts = Path(f).with_suffix("").parts
        local |= set(parts)
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


def is_diff(text: str) -> bool:
    return bool(re.search(r"^\+\+\+ ", text, re.M) and re.search(r"^@@", text, re.M))


def snippet_patch(code: str) -> str:
    """Plain code (no diff) becomes a patch that adds it as a new file."""
    lines = code.splitlines()
    return f"--- /dev/null\n+++ b/{SNIPPET}\n@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{l}\n" for l in lines)


def write_fragments(root: Path, patch: str) -> list[str]:
    """Without the repository, rebuild each changed file as far as the diff shows it (context + added lines)."""
    out: dict[str, list[str]] = {}
    cur = None
    for line in patch.splitlines():
        if line.startswith("+++ "):
            cur = re.sub(r"^b/", "", line[4:].strip())
            cur = None if cur == "/dev/null" else cur
        elif line.startswith("@@") and cur:
            out.setdefault(cur, []).append("")
        elif cur and line[:1] in (" ", "+") and not line.startswith("+++"):
            out[cur].append(line[1:])
    for rel, lines in out.items():
        p = (root / rel).resolve()
        if root.resolve() in p.parents:  # never write outside the workspace
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return list(out)


def verify(source: str, patch: str, llm=None, test_cmd: str | None = None, on_check=None) -> dict:
    """Check a change. With no source, the code is checked on its own: a diff as far as it shows the files,
    or plain code as a new file."""
    work = new_temp_root("agentready-verify-")
    checks: list[dict] = []
    standalone = not (source or "").strip()

    def add(c):
        checks.append(c)
        if on_check:
            on_check(c)

    try:
        if standalone:
            pristine = work / "source"
            pristine.mkdir()
            if not is_diff(patch):
                patch = snippet_patch(patch)
        else:
            pristine = fetch_repo(source, work)
        shim = python_shim_dir(work)
        patched = make_workspace(pristine, work, "patched")
        if not patch.endswith("\n"):
            patch += "\n"
        pfile = work / "change.patch"
        pfile.write_bytes(patch.encode("utf-8"))  # bytes: write_text would turn \n into \r\n on Windows
        files = changed_files(patch)
        fragments: set[str] = set()

        # 1. does the patch apply?
        proc = subprocess.run(["git", "apply", "--whitespace=nowarn", "--ignore-whitespace", "--recount", str(pfile)],
                              cwd=str(patched), capture_output=True, text=True)
        if not files:
            add(_check("applies", "fail", "no code or file changes found", files))
            return {"checks": checks, "files": files}
        if standalone:
            if proc.returncode != 0:  # it changes files we don't have: check what the diff shows
                fragments = set(write_fragments(patched, patch))
            add(_check("applies", "skip", "no repository given: the code is checked on its own", files))
        elif proc.returncode != 0:
            add(_check("applies", "fail", (proc.stderr or "the patch does not apply").strip()[:600], files))
            return {"checks": checks, "files": files}
        else:
            add(_check("applies", "pass", f"{len(files)} file(s)", files))

        # 2. syntax (a fragment of a file only counts when it parses: half a function proves nothing)
        bad, unsure = [], []
        for rel in files:
            p = patched / rel
            if rel.endswith(".py") and p.exists():
                src = p.read_text(encoding="utf-8", errors="replace")
                try:
                    ast.parse(textwrap.dedent(src) if rel in fragments else src)
                except SyntaxError as exc:
                    (unsure if rel in fragments else bad).append(f"{rel}:{exc.lineno}  {exc.msg}")
        if bad:
            add(_check("syntax", "fail", "", bad))
        elif unsure:
            add(_check("syntax", "skip", "only part of the file is in the diff: add the repository to check it", unsure))
        else:
            add(_check("syntax", "pass", ""))

        # 3. did the AI touch tests?
        tests = [f for f in files if is_test_file(f)]
        add(_check("tests_edited", "warn" if tests else "pass", "", tests))

        # 4. risky code + 5. invented packages
        risky = risky_code(patched, files, patch)
        # a leaked secret is never fine; other risky patterns can be deliberate
        add(_check("risky", "fail" if any("secret" in r for r in risky) else "warn" if risky else "pass", "", risky))
        unknown = unknown_imports(patched, files, patch)
        on_pypi = {n: pypi_exists(n) for n in unknown}
        invented = [n for n, ok in on_pypi.items() if ok is False]
        unreached = [n for n, ok in on_pypi.items() if ok is None]
        real = [n for n, ok in on_pypi.items() if ok]
        detail = "; ".join(filter(None, [
            f"not on PyPI: {', '.join(invented)}" if invented else "",
            f"PyPI not reachable to confirm: {', '.join(unreached)}" if unreached else "",
            f"real packages, not installed here: {', '.join(real)}" if real else ""]))
        add(_check("imports", "fail" if invented else "warn" if unreached else "pass", detail, invented + unreached))

        # 6. the repo's own tests, before and after
        info = None
        if standalone:
            add(_check("tests", "skip", "no repository given, so its tests can't run: add the repo for this check"))
        else:
            try:
                info = detect_test_command(pristine, shim, test_cmd)
            except Exception as exc:
                add(_check("tests", "skip", str(exc)[:400]))

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
    err = None
    for max_tokens in (2500, 6000):
        try:
            out = llm.chat_json("super", [{"role": "user", "content": prompt}], max_tokens=max_tokens)
        except LLMError as exc:
            if "HTTP 4" in str(exc):  # bad key, no credits: more room won't help, say why
                raise
            err = exc
            continue
        if isinstance(out, dict) and "verdict" in out:
            return out
    raise ValueError(f"the model did not return a complete review{f' ({err})' if err else ''}")


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
